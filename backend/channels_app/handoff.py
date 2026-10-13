"""Human handoff: keyword rule and pause/resume actions."""
import re
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import Conversation
from .webhooks import emit_event

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


def conversation_event_data(conversation) -> dict:
	contact = conversation.contact
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
	}


def start_handoff(conversation, reason: str) -> bool:
	"""Pause the bot. Returns False (no change, no event) if the conversation is already paused."""
	now = timezone.now()
	cutoff = now - timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS)
	updated = Conversation.objects.filter(id=conversation.id).filter(
		Q(handoff_at__isnull=True) | Q(handoff_at__lte=cutoff)
	).update(handoff_at=now, handoff_reason=(reason or "needs a person").strip()[:255], updated_at=now)
	conversation.refresh_from_db(fields=["handoff_at", "handoff_reason", "updated_at"])
	if updated:
		emit_event(conversation.project, "conversation.handoff", conversation_event_data(conversation))
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
