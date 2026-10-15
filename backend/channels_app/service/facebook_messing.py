

from typing import Any, Dict

from django.http import HttpRequest, HttpResponse, JsonResponse

from accounts.models import Project
from channels_app.models import META_CHANNELS, ChannelCredential, CustomerContact, Message, meta_credentials
from channels_app.facebook_client import get_user_name
from channels_app.reply import schedule_reply
from channels_app.service.conversation_service import ensure_contact_conversation
from channels_app.service.message_service import create_inbound_message
from knowledge.models import KnowledgeBase

import logging
logger = logging.getLogger(__name__)


def handle_facebook_webhook_verification(request: HttpRequest) -> HttpResponse:
	"""
	Handle Facebook webhook verification by dynamically matching verify token.
	
	Facebook sends a GET request with:
	    - hub.mode = "subscribe"
	    - hub.verify_token = The token configured in your channel
	    - hub.challenge = Random string to return back
	
	This function:
	    1. Extracts the verify_token from the request
	    2. Queries all Facebook channels to find one with matching verify_token
	    3. Returns the challenge if found, 403 if not
	
	Args:
	    request: Django HttpRequest with query parameters
	
	Returns:
	    HttpResponse with challenge (200) or 403 Forbidden
	"""
	mode = request.GET.get("hub.mode")
	verify_token = request.GET.get("hub.verify_token")
	challenge = request.GET.get("hub.challenge") or ""
	
	logger.info(f"Facebook webhook verification request: mode={mode}, has_verify_token={bool(verify_token)}")
	
	# Check basic requirements
	if mode != "subscribe" or not verify_token:
		logger.warning(f"Invalid webhook verification request: mode={mode}, verify_token={verify_token}")
		return HttpResponse(status=403)
	
	# Query database directly for channel with matching verify_token in credentials JSON
	# Using JSONField lookup: credentials__verify_token
	channel = ChannelCredential.objects.filter(
		channel__in=META_CHANNELS,  # one callback URL serves Messenger and Instagram
		is_active=True,
		credentials__verify_token=verify_token
	).select_related('platform', 'project').first()
	
	if channel:
		logger.info(
			f"Webhook verification successful for channel: {channel.name} "
			f"(project: {channel.project.name})"
		)
		return HttpResponse(str(challenge).encode("utf-8"))
	
	# No matching verify token found
	logger.warning(
		f"Webhook verification failed: No channel found with verify_token={verify_token}"
	)
	return HttpResponse(status=403)

def handle_facebook_message(payload: Dict[str, Any]) -> None:
	"""
	Process incoming Facebook Messenger messages from webhook payload.
	
	Args:
	    payload: Webhook payload from Facebook containing message events
	
	Flow:
	    1. Extract sender_id (customer) and recipient_id (page_id)
	    2. Find matching ChannelCredential by page_id from credentials
	    3. Get or create CustomerContact
	    4. Create Message record
	    5. Trigger async AI response if applicable
	"""
	# Instagram DMs arrive in the same shape, with object="instagram" and the IG account id as recipient
	channel = "instagram" if payload.get("object") == "instagram" else "facebook"
	default_name = f"{channel.capitalize()} user"
	entry_list = payload.get("entry", [])
	for entry in entry_list:
		messaging = entry.get("messaging", [])
		for evt in messaging:
			sender_id = evt.get("sender", {}).get("id")
			recipient_id = evt.get("recipient", {}).get("id")
			message = evt.get("message", {})
			
			if not (sender_id and recipient_id and message) or message.get("is_echo"):
				continue  # echoes are our own sends (and staff replies from the Meta inbox)

			# Query database directly for channel with matching page_id in credentials JSON
			# Using JSONField lookup: credentials__page_id
			cred = meta_credentials(recipient_id).select_related('platform', 'project', 'knowledge_base').first()
			
			if not cred:
				logger.warning(f"ChannelCredential not found for {channel} account: {recipient_id}")
				continue
			
			project: Project = cred.project
			knowledge_base: KnowledgeBase = cred.knowledge_base
			
			if not knowledge_base:
				logger.warning(f"KnowledgeBase not configured for ChannelCredential page_id: {recipient_id}")
				continue

			contact, _ = CustomerContact.objects.get_or_create(
				project=project, platform=channel, external_id=sender_id
			)
			conversation = contact.conversation
			# Retried on every message until it works: Meta refuses profile reads for users without an
			# app role until pages_messaging has Advanced Access, then starts allowing them.
			# ponytail: one Graph call per message while unnamed; add a retry-after timestamp if that hurts
			if not contact.display_name:
				fields = {"fields": "name,username"} if channel == "instagram" else {}
				contact.display_name = get_user_name(cred.get_credentials().page_access_token, sender_id, **fields)
				if contact.display_name:
					contact.save(update_fields=["display_name"])
					if conversation is not None and conversation.name == default_name:
						conversation.name = contact.display_name
						conversation.save(update_fields=["name"])
			if conversation is None:
				conversation = ensure_contact_conversation(
					contact, knowledge_base, name=contact.display_name or default_name
				)

			# Extract message data
			message_id = message.get("mid", "")

			# Facebook retries webhooks it considers slow/failed; skip already-stored messages
			# ponytail: check-then-insert has a tiny race window; add a partial unique constraint if dupes still appear
			if message_id and Message.objects.filter(
				project=project, direction="in", external_message_id=message_id
			).exists():
				logger.info(f"Duplicate webhook delivery for mid {message_id}, skipping")
				continue
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

			# Only trigger agent reply for text messages (no attachments)
			# Attachments will be handled later
			wants_reply = bool(text) and not has_attachments

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
				conversation=conversation,
				status="pending" if wants_reply else "completed",  # pending until answered (recover_stuck_replies)
			)

			if wants_reply:
				# Enqueue async handler (generate reply + send)
				schedule_reply(msg)
			else:
				logger.info(
					f"Skipping agent reply for message {msg.id}: "
					f"text={bool(text)}, has_attachments={has_attachments}"
				)
	return None