"""Inbound WhatsApp Cloud API webhooks (object="whatsapp_business_account")."""
from typing import Any, Dict

from channels_app.image_reply import wants_photo_reply
from channels_app.media import has_storable_media
from channels_app.media_store import store_message_media
from channels_app.models import CustomerContact, Message, whatsapp_credentials
from channels_app.reply import resolve_unknown, schedule_reply
from channels_app.service.conversation_service import ensure_contact_conversation
from channels_app.service.message_service import create_inbound_message

import logging
logger = logging.getLogger(__name__)


def handle_whatsapp_message(payload: Dict[str, Any]) -> None:
	for entry in payload.get("entry", []):
		for change in entry.get("changes", []):
			value = change.get("value", {})
			phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
			names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
			for status in value.get("statuses", []):
				_handle_status(status)
			for message in value.get("messages", []):
				_handle_message(phone_number_id, message, names.get(message.get("from"), ""))


def _handle_status(status: Dict[str, Any]) -> None:
	"""A status for one of our sends (sent/delivered/read/failed) settles a reply held as unknown.
	Our reference comes back as biz_opaque_callback_data: "out:<message id>:<part>"."""
	ref = (status.get("biz_opaque_callback_data") or "").split(":")
	if len(ref) != 3 or ref[0] != "out" or not ref[2].isdigit():
		return
	state = status.get("status")
	if state in ("sent", "delivered", "read", "failed"):
		resolve_unknown(ref[1], accepted=state != "failed", part=int(ref[2]), provider_id=status.get("id", ""))


def _handle_message(phone_number_id: str, message: Dict[str, Any], name: str) -> None:
	cred = whatsapp_credentials(phone_number_id).select_related("project", "knowledge_base").first()
	if not cred or not cred.knowledge_base:
		logger.warning(f"No active WhatsApp credential with a knowledge base for phone_number_id {phone_number_id}")
		return
	sender, wamid = message.get("from"), message.get("id", "")
	if not sender:
		return
	if wamid and Message.objects.filter(project=cred.project, direction="in", external_message_id=wamid).exists():
		logger.info(f"Duplicate webhook delivery for {wamid}, skipping")
		return

	contact, _ = CustomerContact.objects.get_or_create(project=cred.project, platform="whatsapp", external_id=sender)
	if name and not contact.display_name:  # WhatsApp sends the profile name with every message
		contact.display_name = name[:255]
		contact.save(update_fields=["display_name"])
	conversation = ensure_contact_conversation(
		contact, cred.knowledge_base, name=contact.display_name or "WhatsApp user"
	)

	kind = message.get("type", "")
	text = message.get("text", {}).get("body", "") if kind == "text" else ""
	# Everything else (audio, document, location, reaction...) is stored, not answered, like Messenger attachments;
	# so are images unless image search is on for this number
	attachments = [] if kind == "text" else [{"type": kind, "payload": message.get(kind, {})}]
	photo_reply = wants_photo_reply(cred.project, attachments, cred.image_search_enabled)
	if photo_reply:
		text = (message.get("image") or {}).get("caption", "")
	reply_to_id = message.get("context", {}).get("id")
	reply_to = reply_to_id and Message.objects.filter(contact=contact, external_message_id=reply_to_id).first()
	wants_reply = bool(text) or photo_reply

	msg = create_inbound_message(
		project=cred.project,
		contact=contact,
		text=text,
		external_message_id=wamid,
		timestamp=int(message.get("timestamp") or 0) or None,
		reply_to_message=reply_to or None,
		is_reply=bool(reply_to_id),
		has_attachments=bool(attachments),
		attachment_info=attachments,
		# tasks.get_whatsapp_credential reads phone_number_id
		meta={**message, "phone_number_id": phone_number_id, **({"photo_reply": True} if photo_reply else {})},
		conversation=conversation,
		status="pending" if wants_reply else "completed",
	)
	if has_storable_media(attachments):
		store_message_media.delay(str(msg.id), wants_reply)  # stores the photo/audio in R2, then schedules the reply
	elif wants_reply:
		schedule_reply(msg)
