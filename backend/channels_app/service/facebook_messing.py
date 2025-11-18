

from typing import Any, Dict

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from accounts.models import Project
from channels_app.models import ChannelCredential, CustomerContact, Message
from channels_app.tasks import handle_incoming_message
from channels_app.service.message_service import create_inbound_message
from knowledge.models import KnowledgeBase

import logging
logger = logging.getLogger(__name__)


def handle_facebook_webhook_verification(request: HttpRequest) -> HttpResponse:
	mode = request.GET.get("hub.mode")
	verify_token = request.GET.get("hub.verify_token")
	challenge = request.GET.get("hub.challenge") or ""
	logger.info(f"Facebook webhook verification: {mode == 'subscribe' } and {verify_token == settings.FB_VERIFY_TOKEN}")
	if mode == "subscribe" and verify_token == settings.FB_VERIFY_TOKEN:
		return HttpResponse(str(challenge).encode("utf-8"))
	return HttpResponse(status=403)

def handle_facebook_message(payload: Dict[str, Any]) -> None:
	entry_list = payload.get("entry", [])
	for entry in entry_list:
		messaging = entry.get("messaging", [])
		for evt in messaging:
			sender_id = evt.get("sender", {}).get("id")
			recipient_id = evt.get("recipient", {}).get("id")
			message = evt.get("message", {})
			
			if not (sender_id and recipient_id and message):
				continue

			# Resolve business by page_id (recipient_id)
			cred = ChannelCredential.objects.filter(channel="facebook", page_id=recipient_id).first()
			if not cred:
				logger.warning(f"ChannelCredential not found for page_id: {recipient_id}")
				continue
			
			project: Project = cred.project
			knowledge_base: KnowledgeBase = cred.knowledge_base
			
			if not knowledge_base:
				logger.warning(f"KnowledgeBase not configured for ChannelCredential page_id: {recipient_id}")
				continue

			contact, _ = CustomerContact.objects.get_or_create(
				project=project, platform="facebook", external_id=sender_id
			)

			# Extract message data
			message_id = message.get("mid", "")
			text = message.get("text", "")
			timestamp = evt.get("timestamp")
			attachments = message.get("attachments", [])
			reply_to = message.get("reply_to", {})
			reply_to_mid = reply_to.get("mid") if reply_to else None

			# Check if this is a reply
			is_reply = bool(reply_to_mid)
			
			# Check if message has attachments
			has_attachments = bool(attachments)
			
			# Extract attachment information
			attachment_info = []
			for att in attachments:
				attachment_info.append({
					"type": att.get("type", ""),
					"url": att.get("payload", {}).get("url", ""),
					"payload": att.get("payload", {}),
				})

			# Find the message this is replying to (if applicable)
			reply_to_message = None
			if reply_to_mid:
				try:
					reply_to_message = Message.objects.filter(
						contact=contact,
						external_message_id=reply_to_mid
					).first()
				except Exception as e:
					logger.warning(f"Error finding reply_to message: {e}")

			# Create message record using service
			msg = create_inbound_message(
				project=project,
				contact=contact,
				text=text,
				external_message_id=message_id,
				timestamp=timestamp,
				reply_to_message=reply_to_message,
				is_reply=is_reply,
				has_attachments=has_attachments,
				attachment_info=attachment_info,
				meta=evt,  # Store full event for reference
			)

			# Only trigger agent reply for text messages (no attachments)
			# Attachments will be handled later
			if text and not has_attachments:
				# Enqueue async handler (generate reply + send)
				handle_incoming_message.delay(str(msg.id), str(knowledge_base.id))
			else:
				logger.info(
					f"Skipping agent reply for message {msg.id}: "
					f"text={bool(text)}, has_attachments={has_attachments}"
				)
	return None