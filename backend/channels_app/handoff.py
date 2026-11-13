"""Human handoff: keyword rule and pause/resume actions."""
import re
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .media import has_photo as message_has_photo
from .models import Conversation
from .webhooks import emit_event

HANDOFF_PHOTO_LOOKBACK = 5  # inbound messages checked for a photo: "photo" is often followed by "hello??"
PHOTO_SUFFIX = "; customer sent a photo"

# Needs explicit intent ("talk to a person", "real person", a bare "human") so FAQ questions like
# "customer service hours?" or "human hair?" don't silence the bot; the LLM tool covers the rest.
HANDOFF_PATTERN = re.compile(
	r"\b((talk|speak|chat) (to|with)|connect me (to|with)|transfer me to|put me through to) (a |an |the )?"
	r"(human|real person|person|someone|somebody|agent|representative|manager|staff|operator|customer (service|support))\b"
	r"|\breal person\b|\blive (agent|person)\b|\bhuman (agent|being|please)\b"
	r"|^\s*(human|agent|operator|representative)\s*[.!?]*\s*$",
	re.IGNORECASE,
)


def wants_human(text) -> bool:
	"""English-only rule; run it on the customer's raw text, never on quoted bot messages."""
	return bool(HANDOFF_PATTERN.search(text or ""))


def recent_inbound(conversation, count=HANDOFF_PHOTO_LOOKBACK) -> list:
	"""The customer's newest inbound messages first."""
	return list(conversation.messages.filter(direction="in").order_by("-created_at")[:count])


def conversation_event_data(conversation, last_message=None) -> dict:
	contact = conversation.contact
	recent = recent_inbound(conversation)
	last = last_message or (recent[0] if recent else None)
	return {
		"conversation_id": str(conversation.id),
		"channel": conversation.channel,
		"reason": conversation.handoff_reason,
		"contact": {
			"id": str(contact.id),
			"platform": contact.platform,
			"external_id": contact.external_id,
			"display_name": contact.display_name,
		} if contact else None,
		"last_message_id": str(last.id) if last else None,
		"has_photo": any(message_has_photo(m) for m in recent),
	}


def start_handoff(conversation, reason: str) -> bool:
	"""Pause the bot. Returns False (no change, no event) if the conversation is already paused."""
	now = timezone.now()
	cutoff = now - timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS)
	recent = recent_inbound(conversation)
	reason = (reason or "").strip()[:255]
	if any(message_has_photo(m) for m in recent) and "photo" not in reason.lower():
		# The photo context is appended before truncation, so it survives a long reason
		head = reason[: 255 - len(PHOTO_SUFFIX)].rstrip(" ;")
		reason = f"{head}{PHOTO_SUFFIX}" if head else PHOTO_SUFFIX[2:]
	reason = reason[:255]
	updated = Conversation.objects.filter(id=conversation.id).filter(
		Q(handoff_at__isnull=True) | Q(handoff_at__lte=cutoff)
	).update(handoff_at=now, handoff_reason=reason or "needs a person", updated_at=now)
	conversation.refresh_from_db(fields=["handoff_at", "handoff_reason", "updated_at"])
	if updated:
		emit_event(conversation.project, "conversation.handoff", conversation_event_data(conversation, last_message=recent[0] if recent else None))
	return bool(updated)


def end_handoff(conversation) -> None:
	conversation.handoff_at = None
	conversation.handoff_reason = ""
	conversation.save(update_fields=["handoff_at", "handoff_reason", "updated_at"])
	emit_event(conversation.project, "conversation.resumed", conversation_event_data(conversation))


def note_staff_activity(conversation) -> None:
	"""A staff reply pauses the bot, or restarts the auto-resume clock if already paused."""
	if conversation.is_paused():
		conversation.handoff_at = timezone.now()
		conversation.save(update_fields=["handoff_at", "updated_at"])
	else:
		start_handoff(conversation, "staff took over")
