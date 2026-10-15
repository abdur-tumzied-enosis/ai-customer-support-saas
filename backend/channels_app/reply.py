"""
Channel-agnostic reply pipeline: batching, handoff, agent reply, delivery.

Channels call schedule_reply(msg) for every inbound customer message. Helpers live in
channels_app.tasks and are referenced as `tasks.X` so tests can patch them in one place.
"""
import logging
from typing import Optional

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime

from . import memory, tasks
from .handoff import start_handoff, wants_human
from .models import Conversation, Message
from .service.message_service import create_outbound_message

logger = logging.getLogger(__name__)


def schedule_reply(msg: Message) -> None:
	delay = settings.REPLY_DEBOUNCE_SECONDS.get(msg.conversation.channel, 0)
	process_reply.apply_async(args=[str(msg.conversation_id), str(msg.id)], countdown=delay)


def answered_marker(newest_inbound: Optional[Message]) -> dict:
	"""Outbound meta recording which inbound messages the reply covers (see pending_turn)."""
	return {"answered_through": newest_inbound.created_at.isoformat()} if newest_inbound else {}


def pending_turn(conversation) -> list:
	"""Inbound messages not yet covered by a delivered reply, oldest first.

	Uses the last reply's `answered_through` marker rather than its created_at: a message that
	arrives while the bot is generating is older than the reply but was not answered by it."""
	last_out = (
		Message.objects.filter(conversation=conversation, direction="out")
		.exclude(status="failed").order_by("-created_at").first()
	)
	qs = Message.objects.filter(conversation=conversation, direction="in").select_related("reply_to_message")
	if last_out:
		marker = (last_out.meta or {}).get("answered_through")
		qs = qs.filter(created_at__gt=parse_datetime(marker) if marker else last_out.created_at)
	return list(qs.order_by("created_at"))


def _complete(messages) -> None:
	Message.objects.filter(id__in=[m.id for m in messages]).update(status="completed")


def _send_facebook(conversation, out_msg: Message, reference_msg: Message) -> bool:
	cred = tasks.get_facebook_credential(reference_msg)
	response = tasks.send_text_message(
		page_access_token=cred.get_credentials().page_access_token,
		recipient_psid=conversation.contact.external_id,
		text=out_msg.text,
	) if cred else {}
	if response.get("message_id"):
		out_msg.external_message_id = response["message_id"]
		out_msg.meta = {**out_msg.meta, **response}
		out_msg.save(update_fields=["external_message_id", "meta"])
		return True
	logger.warning(f"Facebook send failed for message {out_msg.id} (credential found: {bool(cred)})")
	out_msg.meta = {**out_msg.meta, "send_error": "facebook send failed"}
	out_msg.save(update_fields=["meta"])
	return False


SENDERS = {
	"facebook": _send_facebook,
	"self": lambda conversation, out_msg, reference_msg: True,  # stored row is the delivery (dashboard, future widget/API)
}


def deliver(conversation, out_msg: Message, reference_msg: Optional[Message] = None) -> bool:
	sender = SENDERS.get(conversation.channel)
	if not sender:
		logger.error(f"No sender for channel {conversation.channel!r}; message {out_msg.id} not delivered")
		return False
	return sender(conversation, out_msg, reference_msg or out_msg)


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def process_reply(self, conversation_id: str, message_id: str) -> None:
	conversation = Conversation.objects.select_related("project", "contact", "knowledge_base").get(id=conversation_id)
	msg = Message.objects.get(id=message_id)

	# Batching: a newer message that gets its own reply task will answer the whole burst.
	# Attachment-only messages never schedule a task, so they must not supersede this one.
	# ponytail: if this task is mid-LLM when a newer message lands, both may answer the older one; add a per-conversation lock if that shows up
	if Message.objects.filter(
		conversation=conversation, direction="in", created_at__gt=msg.created_at, has_attachments=False
	).exclude(text="").exists():
		_complete([msg])
		return

	turn = pending_turn(conversation)
	if not turn:
		_complete([msg])  # already covered by an earlier reply
		return
	if conversation.is_paused():
		_complete(turn)  # staff answer these
		return

	question = "\n".join(tasks.format_message_with_context(m) for m in turn)
	api_calls: list = []
	trace: dict = {}
	handoff_reason = None
	if any(wants_human(m.text) for m in turn):  # raw text only: quoted bot messages must not trigger it
		reply_text = ""
		handoff_reason = "customer asked for a person"
	else:
		try:
			reply_text, ctx = tasks.generate_agent_reply(turn[0], conversation.knowledge_base, question=question)
			api_calls, handoff_reason, trace = ctx.api_call_log, ctx.handoff_reason, ctx.trace
		except Exception as exc:
			api_calls = getattr(exc, "api_calls", [])
			trace = {**getattr(exc, "trace", {}), "error": type(exc).__name__}
			# A retry re-runs the whole agent; never risk sending a POST (order, booking, ticket) twice
			sent_post = any(c.get("method") == "POST" for c in api_calls)
			if not sent_post and self.request.retries < self.max_retries:
				logger.warning(f"Reply generation failed for conversation {conversation.id}, retrying: {exc}")
				raise self.retry(exc=exc)
			logger.error(f"Reply generation failed for conversation {conversation.id}: {exc}", exc_info=True)
			reply_text = ""

	# Staff may have taken over while the bot was generating: their reply wins, the bot's is dropped
	conversation.refresh_from_db(fields=["handoff_at", "handoff_reason"])
	if conversation.is_paused():
		_complete(turn)
		return
	if handoff_reason:
		if not start_handoff(conversation, handoff_reason):
			_complete(turn)  # another task or staff paused it first; don't send a second handoff message
			return
		reply_text = settings.HANDOFF_MESSAGE
	reply_text = reply_text or tasks.FALLBACK_REPLY

	newest = turn[-1]
	with transaction.atomic():
		out_msg = create_outbound_message(
			project=conversation.project,
			contact=conversation.contact,
			text=reply_text,
			meta={
				**answered_marker(newest),
				**({"api_calls": api_calls} if api_calls else {}),
				**({"trace": trace} if trace else {}),
			},
			conversation=conversation,
			id=newest.agent_reply_id or None,
		)
		_complete(turn)
	deliver(conversation, out_msg, newest)
	schedule_memory_update(conversation)


def schedule_memory_update(conversation) -> None:
	if conversation.contact_id:
		contact_id = str(conversation.contact_id)
		transaction.on_commit(lambda: memory.update_contact_memory.delay(contact_id))
