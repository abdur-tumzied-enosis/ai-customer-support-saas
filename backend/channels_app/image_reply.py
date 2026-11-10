"""Answer customer photos: match each one against the catalog and leave a note on the message for the agent."""
import logging

from celery.exceptions import SoftTimeLimitExceeded

from agents.image_search import embed_image, load_image, search_products
from billing.service import charge
from knowledge.models import Product
from usage.utils import record_image_usage

from . import tasks
from .media import fetch_photo, is_photo
from .models import Widget

logger = logging.getLogger(__name__)

MAX_PHOTOS_PER_MESSAGE = 3
MAX_PHOTOS_PER_TURN = 5  # WhatsApp sends one photo per message, so an album is many messages
NO_MATCH_NOTE = "[Customer sent a photo. No catalog product looks similar.]"
UNCHECKED_NOTE = "[Customer sent a photo, but it could not be checked against the catalog.]"


def wants_photo_reply(project, attachment_info: list, channel_enabled: bool) -> bool:
	return bool(project.image_search_on and channel_enabled and any(is_photo(a) for a in attachment_info))


def _photo_note(conversation, msg, att) -> str:
	try:
		kb = conversation.knowledge_base
		hits = search_products(kb.image_collection, embed_image(load_image(fetch_photo(msg, att))))
		products = {str(p.id): p for p in Product.objects.filter(id__in=[pid for pid, _ in hits], kb=kb, status="active")}
		matches = [(products[pid], score) for pid, score in hits if pid in products]  # drops stale and non-active points
	except SoftTimeLimitExceeded:
		raise  # out of time: the reply task sends the fallback (reply._reply)
	except Exception as e:  # MediaError, NotAnImage, Qdrant or DB errors: still answer the customer
		# Type only: the message can carry a signed CDN url (requests' HTTPError)
		logger.warning(f"Photo on message {msg.id} could not be checked against the catalog: {type(e).__name__}")
		return UNCHECKED_NOTE
	record_image_usage(
		str(conversation.project_id), "search", conversation_id=str(conversation.id), message_id=str(msg.id), matched=bool(matches)
	)
	if conversation.channel != "self":
		try:
			charge(conversation.project, "photo_search", key=f"photo_search:{msg.id}", conversation=conversation, trigger_message=msg)
		except Exception:
			logger.exception("Could not charge photo search for message %s", msg.id)
	if not matches:
		return NO_MATCH_NOTE
	listed = "; ".join(f'"{p.name}" (similarity {score:.2f}): {p.description[:300]}' for p, score in matches)
	return f"[Customer sent a photo. Closest catalog products: {listed}]"


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


def annotate_turn(conversation, turn: list) -> None:
	"""Store a catalog-match note in meta["photo_matches"] on each photo message of the turn.
	Skipped entirely (no fetch, no meter) when image search was switched off for the project or the connection
	after the messages were flagged; they then get the generic attachment note. At most MAX_PHOTOS_PER_TURN photos
	are checked; photo messages past that get UNCHECKED_NOTE. Never raises, except celery's SoftTimeLimitExceeded.
	A retry finds the saved notes and does not fetch, search or meter again."""
	pending = [m for m in turn if (m.meta or {}).get("photo_reply") and "photo_matches" not in m.meta]
	if not pending or not conversation.project.image_search_on or not _channel_enabled(conversation, pending[0]):
		return
	budget = MAX_PHOTOS_PER_TURN
	for m in pending:
		try:
			photos = [a for a in m.attachment_info or [] if isinstance(a, dict) and is_photo(a)]
			checked = photos[:min(MAX_PHOTOS_PER_MESSAGE, budget)]
			budget -= len(checked)
			# Past the turn's cap: one "could not be checked" note, so the agent still knows a photo came in
			notes = [_photo_note(conversation, m, a) for a in checked] or ([UNCHECKED_NOTE] if photos else [])
			m.meta["photo_matches"] = " ".join(notes)
			m.save(update_fields=["meta"])
		except SoftTimeLimitExceeded:
			raise
		except Exception as e:
			logger.warning(f"Could not annotate photos on message {m.id}: {type(e).__name__}")
