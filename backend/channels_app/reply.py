"""
Channel-agnostic reply pipeline: batching, handoff, agent reply, delivery.

Channels call schedule_reply(msg) for every inbound customer message. Helpers live in
channels_app.tasks and are referenced as `tasks.X` so tests can patch them in one place.
"""
import logging
from datetime import timedelta
from typing import Optional

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import memory, tasks
from .facebook_client import INSTAGRAM_MAX_TEXT_BYTES, WHATSAPP_MAX_TEXT_CHARS, split_text
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
	# Instagram DMs go through the same Send API (Page token, /me/messages), with a smaller text limit
	cred = tasks.get_facebook_credential(reference_msg, conversation.channel)
	parts = split_text(out_msg.text, max_bytes=INSTAGRAM_MAX_TEXT_BYTES if conversation.channel == "instagram" else None)
	send = cred and (lambda text: tasks.send_text_message(
		page_access_token=cred.get_credentials().page_access_token,
		recipient_psid=conversation.contact.external_id,
		text=text,
	))
	return _send_parts(out_msg, parts, send, conversation.channel)


def _send_whatsapp(conversation, out_msg: Message, reference_msg: Message) -> bool:
	cred = tasks.get_whatsapp_credential(reference_msg)
	creds = cred and cred.get_credentials()
	send = cred and (lambda text: tasks.send_whatsapp_text(
		creds.access_token, creds.phone_number_id, conversation.contact.external_id, text
	))
	return _send_parts(out_msg, split_text(out_msg.text, limit=WHATSAPP_MAX_TEXT_CHARS), send, "whatsapp")


def _send_parts(out_msg: Message, parts: list, send, channel: str) -> bool:
	"""Send `parts` in order with `send(text) -> {"message_id"} | {"error"?}`; records progress on out_msg."""
	sent = out_msg.meta.get("parts_sent", 0)  # resume point when redelivering a partly sent reply
	response = {}
	while send and sent < len(parts):
		response = send(parts[sent])
		if not response.get("message_id"):
			break
		sent += 1
	meta = {k: v for k, v in out_msg.meta.items() if k != "send_error"}
	if len(parts) > 1:
		meta["parts_sent"] = sent
	if parts and sent == len(parts):
		out_msg.external_message_id = response.get("message_id", out_msg.external_message_id)  # last part's id
		out_msg.meta = {**meta, **response}
		out_msg.save(update_fields=["external_message_id", "meta"])
		return True
	logger.warning(f"{channel} send failed for message {out_msg.id} (credential found: {bool(send)}, parts sent: {sent}/{len(parts)})")
	out_msg.meta = {**meta, "send_error": response.get("error") or f"{channel} send failed"}
	out_msg.save(update_fields=["meta"])
	return False


SENDERS = {
	"facebook": _send_facebook,
	"instagram": _send_facebook,
	"whatsapp": _send_whatsapp,
	"self": lambda conversation, out_msg, reference_msg: True,  # stored row is the delivery (dashboard, future widget/API)
}


def deliver(conversation, out_msg: Message, reference_msg: Optional[Message] = None) -> bool:
	sender = SENDERS.get(conversation.channel)
	if not sender:
		logger.error(f"No sender for channel {conversation.channel!r}; message {out_msg.id} not delivered")
		return False
	return sender(conversation, out_msg, reference_msg or out_msg)


REPLY_LOCK_SECONDS = 120  # longer than process_reply's time_limit, so a killed task's lock expires on its own


def _newer_text_message(conversation, after) -> bool:
	"""A newer inbound message with its own reply task (attachment-only messages never get one)."""
	return Message.objects.filter(
		conversation=conversation, direction="in", created_at__gt=after, has_attachments=False
	).exclude(text="").exists()


@shared_task(bind=True, max_retries=2, default_retry_delay=5, soft_time_limit=60, time_limit=90)
def process_reply(self, conversation_id: str, message_id: str) -> None:
	conversation = Conversation.objects.select_related("project", "contact", "knowledge_base").get(id=conversation_id)
	msg = Message.objects.get(id=message_id)

	# Batching: a newer message that gets its own reply task will answer the whole burst
	if _newer_text_message(conversation, msg.created_at):
		_complete([msg])
		return

	# One reply task per conversation at a time, or two could answer the same messages
	lock = f"reply-lock:{conversation_id}"
	if not cache.add(lock, 1, timeout=REPLY_LOCK_SECONDS):
		process_reply.apply_async(args=[conversation_id, message_id], countdown=3)
		return
	try:
		_reply(self, conversation, msg)
	finally:
		cache.delete(lock)


def _reply(task, conversation, msg: Message) -> None:
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
			# Out of time: answer with the fallback now rather than make the customer wait for another full run
			timed_out = isinstance(exc, SoftTimeLimitExceeded)
			if not sent_post and not timed_out and task.request.retries < task.max_retries:
				logger.warning(f"Reply generation failed for conversation {conversation.id}, retrying: {exc}")
				raise task.retry(exc=exc)
			logger.error(f"Reply generation failed for conversation {conversation.id}: {exc}", exc_info=True)
			reply_text = ""

	# A message that landed mid-generation has its own task waiting on the lock, and it answers this turn too.
	# Except after a POST: dropping that reply would hide what was done (order, booking) from the customer.
	if _newer_text_message(conversation, turn[-1].created_at) and not any(c.get("method") == "POST" for c in api_calls):
		return

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
	if not deliver(conversation, out_msg, newest):
		# Failed rows don't count as answered (pending_turn, build_history); retry only the send, never the LLM
		out_msg.status = "failed"
		out_msg.save(update_fields=["status"])
		out_id = str(out_msg.id)
		transaction.on_commit(lambda: redeliver.apply_async(args=[out_id], countdown=10))
	schedule_memory_update(conversation)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def redeliver(self, message_id: str) -> None:
	"""Re-send a bot reply whose delivery failed. Gives up once the conversation has moved on."""
	out_msg = Message.objects.select_related("conversation__contact").get(id=message_id)
	conversation = out_msg.conversation
	# A newer message means a newer reply (or staff) re-answers this turn, since failed rows don't count
	# ponytail: a message landing between this check and the send can still get this turn answered twice
	if (
		out_msg.status != "failed" or conversation.is_paused()
		or conversation.messages.filter(created_at__gt=out_msg.created_at).exists()
	):
		return
	newest_in = conversation.messages.filter(direction="in").order_by("-created_at").first()
	if deliver(conversation, out_msg, newest_in):
		out_msg.status = "completed"
		out_msg.save(update_fields=["status"])
		return
	raise self.retry(exc=RuntimeError(f"Delivery of message {message_id} failed"))


@shared_task
def recover_stuck_replies() -> None:
	"""Re-queue replies lost to a worker crash or restart: inbound messages still pending after 5 minutes.
	Only the last hour, so old rows left pending before this existed aren't suddenly answered."""
	now = timezone.now()
	stuck = Message.objects.filter(
		direction="in", status="pending", conversation__isnull=False,
		created_at__range=(now - timedelta(hours=1), now - timedelta(minutes=5)),
	).order_by("created_at").values_list("conversation_id", "id")
	newest = dict(stuck)  # newest per conversation: its task answers the whole turn
	for conversation_id, message_id in newest.items():
		process_reply.delay(str(conversation_id), str(message_id))
	if newest:
		logger.warning(f"Re-queued {len(newest)} stuck replies")


def schedule_memory_update(conversation) -> None:
	if conversation.contact_id:
		contact_id = str(conversation.contact_id)
		transaction.on_commit(lambda: memory.update_contact_memory.delay(contact_id))
