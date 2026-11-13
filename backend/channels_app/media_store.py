"""Keep customer photos and voice notes in R2 before the reply runs: Meta media is downloaded (Meta's own URLs expire),
widget/test-chat uploads are moved out of tmp/. Photos become display + thumb WebP variants; audio is stored as is."""
import logging
import os
from urllib.parse import urlsplit
from uuid import uuid4

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from agents.image_search import MAX_IMAGE_BYTES, NotAnImage, make_send_variant, make_variants
from core.storage import AUDIO_MAX_BYTES, delete_keys, thumb_key

from .media import fetch_attachment, is_photo, is_storable, read_stored
from .models import Conversation, Message
from .reply import schedule_reply

logger = logging.getLogger(__name__)

AUDIO_EXTS = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/mp4": "m4a", "audio/aac": "aac", "audio/mpeg": "mp3", "audio/amr": "amr"}


def _needs_storing(att) -> bool:
	return isinstance(att, dict) and is_storable(att) and (not att.get("path") or att["path"].startswith("tmp/"))


def _audio_ext(att: dict) -> str:
	mime = str((att.get("payload") or {}).get("mime_type") or "").split(";")[0].strip().lower()
	if mime in AUDIO_EXTS:
		return AUDIO_EXTS[mime]
	ext = os.path.splitext(urlsplit(att.get("url") or "").path)[1].lstrip(".").lower()  # Messenger sends no mime type
	return ext if ext in AUDIO_EXTS.values() else "bin"


def _store(msg: Message, att: dict) -> str:
	"""Store one attachment; returns its new key. Raises MediaError / NotAnImage."""
	folder = f"chat_media/{msg.project.slug}"
	tmp = att.get("path") or ""
	if not is_photo(att):
		data = fetch_attachment(msg, att, AUDIO_MAX_BYTES)
		return default_storage.save(f"{folder}/{uuid4().hex}.{_audio_ext(att)}", ContentFile(data))
	try:
		data = read_stored(tmp) if tmp else fetch_attachment(msg, att, MAX_IMAGE_BYTES)
		display, thumb = make_variants(data)
	except NotAnImage:
		delete_keys(tmp)  # an upload that lied about being a photo
		raise
	name = default_storage.save(f"{folder}/{uuid4().hex}.webp", ContentFile(display))
	default_storage.save(thumb_key(name), ContentFile(thumb))
	delete_keys(tmp)
	return name


def store_outbound_photo(conversation: Conversation, att: dict) -> str:
	"""Store one staff photo upload right now and return its key: a JPEG (what chat platforms accept, unlike the
	WebP a customer photo becomes) plus a WebP thumb. The temporary upload is deleted. Raises MediaError (bad or
	oversized upload) and NotAnImage (not a real photo), so the caller can answer 400 before any message exists.

	Takes the conversation, not a Message: it runs before the row is created, so a failure leaves no row behind."""
	data = read_stored(att["path"])
	try:
		display = make_send_variant(data)
	except NotAnImage:
		delete_keys(att["path"])  # an upload that lied about being a photo: do not leave it in tmp/
		raise
	name = default_storage.save(f"chat_media/{conversation.project.slug}/{uuid4().hex}.jpg", ContentFile(display))
	default_storage.save(thumb_key(name), ContentFile(make_variants(data)[1]))
	delete_keys(att["path"])
	return name


@shared_task(soft_time_limit=60, time_limit=90)
def store_message_media(message_id: str, reply: bool) -> None:
	"""Store each photo/audio attachment of an inbound message, then schedule its reply (always, even if storing
	failed: the reply falls back to Meta's copy, or says the photo could not be checked)."""
	msg = Message.objects.select_related("project", "conversation").filter(id=message_id).first()
	if msg is None:
		return
	try:
		changed = False
		for att in msg.attachment_info or []:
			if not _needs_storing(att):
				continue
			try:
				att["path"] = _store(msg, att)
				changed = True
			except SoftTimeLimitExceeded:
				raise
			except Exception as e:
				logger.warning(f"Could not store {att.get('type')} attachment of message {message_id}: {e}")
		if changed:
			Message.objects.filter(id=msg.id).update(attachment_info=msg.attachment_info)
	finally:
		if reply:
			schedule_reply(msg)
