"""Keep customer photos and voice notes in R2 before the reply runs: Meta media is downloaded (Meta's own URLs expire),
widget/test-chat uploads are moved out of tmp/. Photos become display + thumb WebP variants; audio is stored as is."""
import logging
import math
import os
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.files.base import ContentFile
from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from agents.image_search import MAX_IMAGE_BYTES, NotAnImage, make_send_variant, make_variants
from agents.transcription import TranscriptionError, audio_format, audio_seconds, resolve_transcription_model, transcribe
from billing.models import CreditTransaction, PricingSettings
from billing.service import InsufficientCredits, charge_if_affordable, grant
from core.storage import AUDIO_MAX_BYTES, delete_keys, thumb_key

from usage.utils import finish_attempt, start_attempt

from .media import fetch_attachment, is_photo, is_storable, read_stored, transcription_on
from .models import Conversation, Message
from .reply import pending_turn, schedule_reply

logger = logging.getLogger(__name__)

TRANSCRIPTION_LOCK_SECONDS = 180
TERMINAL_STATES = {"transcribed", "failed", "failed_uncertain", "empty", "unmeasurable", "over_cap", "unaffordable"}
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


def _is_audio(att) -> bool:
	return isinstance(att, dict) and att.get("type") == "audio"


def update_message(message_id: str, change) -> Message:
	"""Lock the row, let `change(msg)` mutate meta/text, save both."""
	with transaction.atomic():
		msg = Message.objects.select_for_update().get(id=message_id)
		change(msg)
		msg.save(update_fields=["meta", "text"])
		return msg


def _set_state(message_id: str, index: int, status: str, **fields) -> None:
	def change(msg):
		states = msg.meta.setdefault("transcription", {})
		states[str(index)] = {**states.get(str(index), {}), "status": status, **fields}
	update_message(message_id, change)


def refund_transcription(project, message_id: str, index: int, reason: str) -> None:
	"""Give back what the charge row took (none if never charged). Never raises."""
	try:
		txn = CreditTransaction.objects.filter(idempotency_key=f"transcription:{message_id}:{index}").first()
		if txn:
			grant(project, -txn.amount, "adjust", key=f"transcription_refund:{message_id}:{index}", note=f"Transcription refund: {reason}")
	except Exception as e:
		logger.error(f"Transcription refund of message {message_id} attachment {index} failed: {type(e).__name__}")


def _move_if_processing(message_id: str, index: int, new_status: str, allow_absent: bool = False, **fields) -> bool:
	"""Under the row lock, change the entry to `new_status` only if it is still `processing` (or absent, when
	allowed). Returns whether it moved: the caller refunds only then, so a settled entry is never overwritten."""
	moved = False

	def change(msg):
		nonlocal moved
		states = msg.meta.setdefault("transcription", {})
		entry = states.get(str(index))
		if (entry is None and allow_absent) or (entry or {}).get("status") == "processing":
			states[str(index)] = {**(entry or {}), "status": new_status, **fields}
			moved = True
	update_message(message_id, change)
	return moved


def _claim_processing(message_id: str, index: int, **fields) -> bool:
	"""Compare-and-set under the row lock: move to `processing` only from no state. The cache claim is best-effort,
	this is what stops a second provider call."""
	claimed = False

	def change(msg):
		nonlocal claimed
		states = msg.meta.setdefault("transcription", {})
		if str(index) in states:
			return
		states[str(index)] = {"status": "processing", "started_at": timezone.now().isoformat(), **fields}
		claimed = True
	update_message(message_id, change)
	return claimed


def maybe_schedule_reply(message_id: str) -> None:
	"""Once every voice note has settled, answer the newest pending message (not necessarily this one)."""
	msg = Message.objects.select_related("conversation").filter(id=message_id).first()
	if msg is None:
		return
	states = (msg.meta or {}).get("transcription", {})
	if any(_is_audio(a) and states.get(str(i), {}).get("status") not in TERMINAL_STATES for i, a in enumerate(msg.attachment_info or [])):
		return
	turn = pending_turn(msg.conversation)
	if turn and cache.add(f"transcribe-done:{message_id}", 1, timeout=3600):
		schedule_reply(turn[-1])


@shared_task(soft_time_limit=60, time_limit=90)
def transcribe_attachment(message_id: str, index: int) -> None:
	"""Transcribe one voice note: charge first, call the provider once, refund on any failure."""
	if not cache.add(f"transcribe:{message_id}:{index}", 1, timeout=TRANSCRIPTION_LOCK_SECONDS):
		return
	msg = Message.objects.select_related("project", "conversation").filter(id=message_id).first()
	atts = (msg.attachment_info or []) if msg else []
	if not (0 <= index < len(atts) and _is_audio(atts[index])):
		return
	project = msg.project
	claimed = False
	try:
		status = (msg.meta or {}).get("transcription", {}).get(str(index), {}).get("status")
		if status in TERMINAL_STATES:
			return
		if status == "processing":  # a dead run: the provider may have been called, never call it again
			if _move_if_processing(message_id, index, "failed_uncertain"):
				refund_transcription(project, message_id, index, "interrupted")
			return
		path = atts[index].get("path")
		if not path:
			_set_state(message_id, index, "failed")
			return
		data = read_stored(path, AUDIO_MAX_BYTES)
		fmt = audio_format(data)
		seconds = audio_seconds(data, fmt) if fmt else None
		if seconds is None:
			_set_state(message_id, index, "unmeasurable")
			return
		if seconds > settings.TRANSCRIPTION_MAX_SECONDS:
			_set_state(message_id, index, "over_cap", seconds=seconds)
			return
		units = max(1, math.ceil(seconds / PricingSettings.get().transcription_seconds_per_credit))  # the charge applies the price
		model = resolve_transcription_model()
		if not _claim_processing(message_id, index, model=model, seconds=seconds, units=units):
			return  # another run holds it, or it already settled: no charge, no provider call
		claimed = True
		try:
			charge_if_affordable(project, "transcription", key=f"transcription:{message_id}:{index}", units=units, conversation=msg.conversation, trigger_message=msg)
		except InsufficientCredits:  # shortcut: a crash between processing and unaffordable makes this note failed_uncertain with no charge, so the customer gets the failure reply instead of a handoff; upgrade by writing unaffordable before the claim
			_set_state(message_id, index, "unaffordable", seconds=seconds, units=units)
			return
		attempt = start_attempt(str(project.id), model, seconds=seconds, conversation_id=str(msg.conversation_id), message_id=message_id, attachment_index=index)
		try:
			out = transcribe(data, fmt, model)
		except TranscriptionError:
			finish_attempt(attempt, tokens=0, cost_usd=None, status="failed")
			if _move_if_processing(message_id, index, "failed"):
				refund_transcription(project, message_id, index, "provider error")
			return
		finish_attempt(attempt, status="reported", tokens=out.tokens, cost_usd=out.cost_usd, usage=out.usage)
		text = out.text.strip()
		if not text:
			if _move_if_processing(message_id, index, "empty"):
				refund_transcription(project, message_id, index, "empty transcript")
			return

		def done(m):
			entry = m.meta.setdefault("transcription", {}).get(str(index), {})
			if entry.get("status") != "processing":  # swept and refunded meanwhile: never turn that into a free transcript
				return
			m.text = " ".join(filter(None, [m.text, text]))
			m.meta.setdefault("transcript_texts", {})[str(index)] = text
			m.meta["transcription"][str(index)] = {**entry, "status": "transcribed", "attempt": attempt}
		update_message(message_id, done)
	except Exception as e:
		logger.warning(f"Transcription of message {message_id} attachment {index} failed: {type(e).__name__}")
		if _move_if_processing(message_id, index, "failed", allow_absent=not claimed):
			refund_transcription(project, message_id, index, "error")
	finally:
		maybe_schedule_reply(message_id)


HARD_TIME_LIMIT = 90
SWEEP_FAILED = {"failed", "failed_uncertain", "empty"}


@shared_task
def reconcile_transcriptions() -> None:
	"""Refund charges stranded by a dead run (`processing`) and failures whose refund never landed. Never raises."""
	now = timezone.now()
	try:
		batch = list(Message.objects.select_related("project").filter(
			meta__has_key="transcription", created_at__range=(now - timedelta(hours=2), now - timedelta(minutes=10)),
		).order_by("created_at")[:100])
	except Exception as e:
		logger.error(f"Transcription sweep query failed: {type(e).__name__}")
		return
	for msg in batch:
		try:
			touched = False
			for i, entry in list((msg.meta.get("transcription") or {}).items()):
				status = entry.get("status")
				if status == "processing":
					started = parse_datetime(entry.get("started_at") or "")
					if started and started > now - timedelta(seconds=HARD_TIME_LIMIT + 300):
						continue  # a live run may still be inside its time limit
					if _move_if_processing(str(msg.id), int(i), "failed_uncertain"):
						refund_transcription(msg.project, str(msg.id), i, "interrupted")
						touched = True
				elif status in SWEEP_FAILED:
					charged = CreditTransaction.objects.filter(idempotency_key=f"transcription:{msg.id}:{i}").exists()
					refunded = CreditTransaction.objects.filter(idempotency_key=f"transcription_refund:{msg.id}:{i}").exists()
					if charged and not refunded:
						refund_transcription(msg.project, str(msg.id), i, "retried refund")
			if touched:
				maybe_schedule_reply(str(msg.id))
		except Exception as e:
			logger.error(f"Transcription sweep of message {msg.id} failed: {type(e).__name__}")


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
			audio = [i for i, a in enumerate(msg.attachment_info or []) if _is_audio(a)]
			if audio and transcription_on(msg.project):
				for i in audio:  # stored or not: an unstored one fails in the task, which then drives the reply
					transcribe_attachment.delay(str(msg.id), i)
			else:
				schedule_reply(msg)
