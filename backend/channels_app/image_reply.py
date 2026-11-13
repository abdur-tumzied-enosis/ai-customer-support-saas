"""Answer customer photos: match each one against the catalog, number the photos of a turn and leave a note on each
message for the agent. Photos the reply model needs to see are chosen here (vision_images) and sent by channels_app.reply."""
import logging
import re
from datetime import datetime, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from agents.embeddings_api import embed_image
from agents.image_search import load_image, search_products
from agents.llm import vision_route
from billing.service import charge
from knowledge.models import Product
from usage.utils import record_image_usage

from . import tasks
from .media import fetch_photo, is_photo
from .models import Widget

logger = logging.getLogger(__name__)

MAX_PHOTOS_PER_MESSAGE = 3
MAX_PHOTOS_PER_TURN = 5  # WhatsApp sends one photo per message, so an album is many messages
PHOTO_NOTES_KEY = "photo_notes"
PHOTO_VISION_KEY = "photo_vision"
VISION_DAY_SECONDS = 24 * 60 * 60

DAMAGE_PATTERN = re.compile(
	r"\b(damag\w*|broke\w*|broken|crack\w*|torn|rip\w*|defect\w*|fault\w*|leak\w*|stain\w*|"
	r"refund\w*|return\w*|exchange\w*|complain\w*|complaint\w*|warranty|fake|counterfeit|"
	r"wrong (item|product|order)|missing|not working|doesn'?t work|expired|"
	r"bhanga|vanga|bhang\w*|vang\w*|venge|nosto|kharap|ferot|kaj kore na|kaj korche na|"
	r"return korte|change korte|change korte chai|venge geche)\b",
	re.IGNORECASE,
)
BANGLA_DAMAGE = ("ভাঙ", "ভেঙ", "নষ্ট", "খারাপ", "ছিঁড়", "ফাট", "ক্ষতিগ্রস্ত", "রিফান্ড", "ফেরত", "বদল", "অভিযোগ", "ওয়ারেন্টি", "ঠিক নেই", "করছে না")


def wants_photo_reply(project, attachment_info: list, channel_enabled: bool) -> bool:
	return bool(project.image_search_on and channel_enabled and any(is_photo(a) for a in attachment_info))


def mentions_damage(text: str) -> bool:
	return bool(DAMAGE_PATTERN.search(text or "")) or any(word in (text or "") for word in BANGLA_DAMAGE)


def recent_damage_text(conversation, turn) -> str:
	"""Inbound text from the last VISION_DAMAGE_LOOKBACK_MINUTES, excluding this turn. Never quotes the bot."""
	since = timezone.now() - timedelta(minutes=settings.VISION_DAMAGE_LOOKBACK_MINUTES)
	recent = (conversation.messages.filter(direction="in", created_at__gte=since)
		.exclude(id__in=[m.id for m in turn]).order_by("-created_at")[:10])
	return " ".join(m.text or "" for m in recent)


def _live_stamps(stamps, cutoff) -> list:
	live = []
	for stamp in stamps if isinstance(stamps, list) else []:
		try:
			if datetime.fromisoformat(stamp) > cutoff:
				live.append(stamp)
		except (TypeError, ValueError):
			continue  # a malformed stamp counts for nothing
	return live


def vision_day_remaining(conversation) -> int:
	"""Photos this conversation may still get in the rolling 24h. 0 when the counter cannot be read (fail closed)."""
	try:
		cutoff = timezone.now() - timedelta(seconds=VISION_DAY_SECONDS)
		live = _live_stamps(cache.get(f"vision-day:{conversation.id}"), cutoff)
		return max(0, settings.VISION_MAX_IMAGES_PER_CONVERSATION_DAY - len(live))
	except Exception as e:
		logger.warning(f"Could not read the photo daily cap for conversation {conversation.id}: {type(e).__name__}")
		return 0


def vision_readiness(conversation, msg) -> str:
	"""Empty string when the turn may attach photos, else the reason: tenant | channel | model."""
	try:
		if not conversation.project.vision_fallback_on:
			return "tenant"
	except SoftTimeLimitExceeded:
		raise
	except Exception as e:
		logger.warning(f"Could not read the photo help setting for conversation {conversation.id}: {type(e).__name__}")
		return "channel"  # fail closed: no images
	if not _channel_enabled(conversation, msg):
		return "channel"
	if vision_route() is None:
		return "model"
	return ""


def _entry(index: int, path: str, **fields) -> dict:
	entry = {
		"index": index, "reason": "unchecked", "path": path, "matches": "", "score": None, "damage": False,
		"trigger": "", "attachable": False, "attached": False, "not_attached": "", "note": "",
	}
	entry.update(fields)
	return entry


def _photo_note(conversation, msg, att, index, damage=False, damage_from_history=False, vision_ready=False) -> dict:
	path = (att.get("path") if isinstance(att, dict) else None) or ""
	try:
		kb = conversation.knowledge_base
		embedded = embed_image(load_image(fetch_photo(msg, att)))
		hits = search_products(kb.vector_collection, embedded.vectors[0])
		products = {str(p.id): p for p in Product.objects.filter(id__in=[pid for pid, _ in hits], kb=kb, status="active")}
		matches = [(products[pid], score) for pid, score in hits if pid in products]  # drops stale and non-active points
	except SoftTimeLimitExceeded:
		raise  # out of time: the reply task sends the fallback (reply._reply)
	except Exception as e:  # MediaError, NotAnImage, Qdrant or DB errors: still answer the customer
		# Type only: the message can carry a signed CDN url (requests' HTTPError)
		logger.warning(f"Photo on message {msg.id} could not be checked against the catalog: {type(e).__name__}")
		return _entry(index, path)
	record_image_usage(
		str(conversation.project_id), "search", tokens=embedded.tokens, cost_usd=embedded.cost_usd, model=embedded.model, conversation_id=str(conversation.id), message_id=str(msg.id), matched=bool(matches)
	)
	if conversation.channel != "self":
		try:
			charge(conversation.project, "photo_search", key=f"photo_search:{msg.id}", conversation=conversation, trigger_message=msg)
		except Exception:
			logger.exception("Could not charge photo search for message %s", msg.id)
	score = matches[0][1] if matches else None
	if not matches:
		reason = "no_match"
	elif score < settings.IMAGE_SEARCH_MIN_SCORE + settings.VISION_BORDERLINE_MARGIN:
		reason = "borderline"  # search_products already drops hits below IMAGE_SEARCH_MIN_SCORE
	else:
		reason = "match"
	if reason == "match" and damage:
		trigger = "damage_history" if damage_from_history else "damage"
	else:
		trigger = reason
	listed = "; ".join(f'"{p.name}" (similarity {s:.2f}): {p.description[:300]}' for p, s in matches)
	attachable = bool(vision_ready and path and (reason in ("no_match", "borderline") or damage))
	return _entry(
		index, path, reason=reason, matches=listed, score=score, damage=damage, trigger=trigger, attachable=attachable,
		not_attached="" if attachable else ("settings" if (not vision_ready or not path) else ""),
	)


def _render_note(entry) -> str:
	i = entry["index"]
	if entry["reason"] == "unchecked":
		return f"[Customer sent photo {i}, but it could not be checked against the catalog.]"
	if entry["reason"] == "no_match":
		base = f"[Customer sent photo {i}. No catalog product looks similar"
	else:
		base = f"[Customer sent photo {i}. Closest catalog products: {entry['matches']}"
	if entry["attached"]:
		if entry["reason"] == "borderline":
			tail = " Photo attached: the match is not close enough to rely on."
		elif entry["damage"] and entry["reason"] == "match":
			tail = " Photo attached: the customer's message suggests damage or a problem, so look at the item's condition."
		else:
			tail = " Photo attached."
	elif entry["not_attached"] == "limit":
		tail = " Photo not attached (limit reached)."
	elif entry["not_attached"] == "daily":
		tail = " Photo not attached (daily limit)."
	elif entry["not_attached"] == "prepare":
		tail = " Photo not attached (could not be prepared)."
	else:
		tail = " Photo not attached."
	return f"{base}.{tail}]"


def _channel_enabled(conversation, msg) -> bool:
	"""The connection's own image-search toggle, read now (it may have changed since the message was flagged).
	The test chat ("self") has none. Any error counts as off."""
	try:
		channel = conversation.channel
		if channel == "self":
			return True
		if channel in ("facebook", "instagram"):
			conn = tasks.get_facebook_credential(msg, channel)
		elif channel == "whatsapp":
			conn = tasks.get_whatsapp_credential(msg)
		elif channel == "widget":
			widget_id = conversation.contact.external_id.split(":")[0]
			conn = Widget.objects.filter(id=widget_id, project_id=conversation.project_id).first()
		else:
			return False
		return bool(conn and conn.image_search_enabled)
	except SoftTimeLimitExceeded:
		raise
	except Exception as e:
		logger.warning(f"Could not read the image search toggle for conversation {conversation.id}: {type(e).__name__}")
		return False


def _is_annotated(m) -> bool:
	meta = m.meta or {}
	return PHOTO_NOTES_KEY in meta or "photo_matches" in meta


def annotate_turn(conversation, turn: list) -> None:
	"""Number every photo of the turn and store one note per photo in meta["photo_notes"] on its message.
	Phase 1 checks the photos (at most MAX_PHOTOS_PER_TURN; the rest get an "unchecked" note). Phase 2 picks the
	attachable photos within the turn and daily caps, so a note written now already says when the cap drops a
	photo. Selection is provisional: vision_images can still drop one on a read failure and rewrites its note.
	Retries keep the saved notes, count the photos already checked against the turn budget, and never fetch or
	search again. Skipped entirely (no fetch, no meter) when image search is off for the project or the connection;
	those messages keep the generic attachment note. Never raises, except celery's SoftTimeLimitExceeded."""
	pending = [m for m in turn if (m.meta or {}).get("photo_reply") and not _is_annotated(m)]
	if not pending or not conversation.project.image_search_on or not _channel_enabled(conversation, pending[0]):
		return
	vision_ready = not vision_readiness(conversation, turn[0])
	turn_text = " ".join(m.text or "" for m in turn)
	current_damage = mentions_damage(turn_text)
	damage = current_damage or mentions_damage(recent_damage_text(conversation, turn))
	damage_from_history = damage and not current_damage

	already_checked = sum(len([a for a in m.attachment_info or [] if isinstance(a, dict) and is_photo(a)]) for m in turn if _is_annotated(m))
	already_vision = sum(len((m.meta or {}).get(PHOTO_VISION_KEY) or []) for m in turn if _is_annotated(m))
	budget = MAX_PHOTOS_PER_TURN - already_checked
	index = 0
	plan = []  # (message, entries) for each pending message
	for m in turn:
		photos = [a for a in m.attachment_info or [] if isinstance(a, dict) and is_photo(a)]
		if _is_annotated(m) or not (m.meta or {}).get("photo_reply"):
			index += len(photos)  # keep numbering stable across retries
			continue
		try:
			checked = photos[:min(MAX_PHOTOS_PER_MESSAGE, max(0, budget))]
			budget -= len(checked)
			entries = []
			for att in checked:
				index += 1
				entries.append(_photo_note(conversation, m, att, index, damage=damage, damage_from_history=damage_from_history, vision_ready=vision_ready))
			for att in photos[len(checked):]:  # past the turn budget: still numbered, never attachable
				index += 1
				entries.append(_entry(index, (att.get("path") or "")))
			plan.append((m, entries))
		except SoftTimeLimitExceeded:
			raise
		except Exception as e:
			logger.warning(f"Could not annotate photos on message {m.id}: {type(e).__name__}")

	if vision_ready:
		turn_slots = settings.VISION_MAX_IMAGES_PER_TURN - already_vision
		day_left = vision_day_remaining(conversation)
		for _, entries in plan:
			for entry in entries:
				if not entry["attachable"]:
					continue
				if turn_slots <= 0:
					entry["not_attached"] = "limit"
				elif day_left <= 0:
					entry["not_attached"] = "daily"
				else:
					entry["attached"] = True
					turn_slots -= 1
					day_left -= 1

	for m, entries in plan:
		for entry in entries:
			entry["note"] = _render_note(entry)
		m.meta = {**(m.meta or {}), PHOTO_NOTES_KEY: entries,
			PHOTO_VISION_KEY: [{"index": e["index"], "path": e["path"]} for e in entries if e["attached"]]}
		m.save(update_fields=["meta"])
