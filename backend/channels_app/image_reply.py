"""Answer customer photos: match each one against the catalog and leave a note on the message for the agent."""
import logging

from celery.exceptions import SoftTimeLimitExceeded

from agents.image_search import embed_image, load_image, search_products
from knowledge.models import Product
from usage.utils import record_image_usage

from .media import fetch_photo, is_photo

logger = logging.getLogger(__name__)

MAX_PHOTOS_PER_MESSAGE = 3
NO_MATCH_NOTE = "[Customer sent a photo. No catalog product looks similar.]"
UNCHECKED_NOTE = "[Customer sent a photo, but it could not be checked against the catalog.]"


def wants_photo_reply(project, attachment_info: list, channel_enabled: bool) -> bool:
	return bool(project.image_search_on and channel_enabled and any(is_photo(a) for a in attachment_info))


def _photo_note(conversation, msg, att) -> str:
	try:
		kb = conversation.knowledge_base
		hits = search_products(kb.image_collection, embed_image(load_image(fetch_photo(msg, att))))
		products = {str(p.id): p for p in Product.objects.filter(id__in=[pid for pid, _ in hits], kb=kb)}
		matches = [(products[pid], score) for pid, score in hits if pid in products]  # drops stale points
	except SoftTimeLimitExceeded:
		raise  # out of time: the reply task sends the fallback (reply._reply)
	except Exception as e:  # MediaError, NotAnImage, Qdrant or DB errors: still answer the customer
		# Type only: the message can carry a signed CDN url (requests' HTTPError)
		logger.warning(f"Photo on message {msg.id} could not be checked against the catalog: {type(e).__name__}")
		return UNCHECKED_NOTE
	record_image_usage(str(conversation.project_id), "search", message_id=str(msg.id), matched=bool(matches))
	if not matches:
		return NO_MATCH_NOTE
	listed = "; ".join(f'"{p.name}" (similarity {score:.2f}): {p.description[:300]}' for p, score in matches)
	return f"[Customer sent a photo. Closest catalog products: {listed}]"


def annotate_turn(conversation, turn: list) -> None:
	"""Store a catalog-match note in meta["photo_matches"] on each photo message of the turn.
	Never raises, except celery's SoftTimeLimitExceeded. A retry finds the saved note and does not fetch,
	search or meter again."""
	for m in turn:
		try:
			if not (m.meta or {}).get("photo_reply") or "photo_matches" in m.meta:
				continue
			photos = [a for a in m.attachment_info or [] if isinstance(a, dict) and is_photo(a)]
			m.meta["photo_matches"] = " ".join(_photo_note(conversation, m, a) for a in photos[:MAX_PHOTOS_PER_MESSAGE])
			m.save(update_fields=["meta"])
		except SoftTimeLimitExceeded:
			raise
		except Exception as e:
			logger.warning(f"Could not annotate photos on message {m.id}: {type(e).__name__}")
