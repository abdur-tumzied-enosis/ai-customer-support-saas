"""Human handoff: keyword rule and pause/resume actions."""
import re

from django.utils import timezone

from .webhooks import emit_event

HANDOFF_PATTERN = re.compile(
	r"\b(human|real person|live (agent|person)|customer (service|support)|operator"
	r"|(talk|speak) to (a |an )?(someone|somebody|person|agent|representative|manager))\b",
	re.IGNORECASE,
)


def wants_human(text) -> bool:
	"""English-only rule; the agent's escalate_to_human tool covers everything else."""
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


def start_handoff(conversation, reason: str) -> None:
	conversation.handoff_at = timezone.now()
	conversation.handoff_reason = (reason or "needs a person").strip()[:255]
	conversation.save(update_fields=["handoff_at", "handoff_reason", "updated_at"])
	emit_event(conversation.project, "conversation.handoff", conversation_event_data(conversation))


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
