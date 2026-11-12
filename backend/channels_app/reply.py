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
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from billing.service import charge_delivered_reply, price_of, shopper_blocked

from . import image_reply, memory, tasks
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
	send = cred and (lambda text, ref: tasks.send_text_message(
		page_access_token=cred.get_credentials().page_access_token,
		recipient_psid=conversation.contact.external_id,
		text=text,
	))
	return _send_parts(out_msg, parts, send, conversation.channel)


def _send_whatsapp(conversation, out_msg: Message, reference_msg: Message) -> bool:
	cred = tasks.get_whatsapp_credential(reference_msg)
	creds = cred and cred.get_credentials()
	send = cred and (lambda text, ref: tasks.send_whatsapp_text(
		creds.access_token, creds.phone_number_id, conversation.contact.external_id, text, callback_data=ref
	))
	return _send_parts(out_msg, split_text(out_msg.text, limit=WHATSAPP_MAX_TEXT_CHARS), send, "whatsapp")


def _send_parts(out_msg: Message, parts: list, send, channel: str) -> bool:
	"""Send `parts` in order with `send(text, ref) -> {"message_id"} | {"error"?, "unknown"?}`; records progress on out_msg.
	`ref` ("out:<message id>:<part>") is the stable per-part reference. meta["inflight"] is written before each request
	and cleared once its outcome is known: if it is still set later, that part may or may not have been delivered."""
	sent = out_msg.meta.get("parts_sent", 0)  # resume point when redelivering a partly sent reply
	response = {}
	while send and sent < len(parts):
		out_msg.meta = {**out_msg.meta, "inflight": sent, "parts_total": len(parts)}
		out_msg.save(update_fields=["meta"])
		response = send(parts[sent], f"out:{out_msg.id}:{sent}")
		if response.get("unknown"):
			break  # may have been accepted: leave "inflight" set, attempt_delivery holds the reply instead of resending
		out_msg.meta = {k: v for k, v in out_msg.meta.items() if k != "inflight"}
		if not response.get("message_id"):
			break
		sent += 1
		out_msg.meta = {**out_msg.meta, "parts_sent": sent}  # saved per part: a crash resumes after the last accepted part
		out_msg.save(update_fields=["meta"])
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
	"self": lambda conversation, out_msg, reference_msg: True,  # stored row is the delivery (dashboard)
	"widget": lambda conversation, out_msg, reference_msg: True,  # the visitor's iframe polls for it
}


def deliver(conversation, out_msg: Message, reference_msg: Optional[Message] = None) -> bool:
	sender = SENDERS.get(conversation.channel)
	if not sender:
		logger.error(f"No sender for channel {conversation.channel!r}; message {out_msg.id} not delivered")
		return False
	return sender(conversation, out_msg, reference_msg or out_msg)


REPLY_LOCK_SECONDS = 120  # longer than process_reply's time_limit, so a killed task's lock expires on its own


def _newer_text_message(conversation, after) -> bool:
	"""A newer inbound message with its own reply task: plain text, or a photo flagged for a reply
	(other attachment messages never get one)."""
	return Message.objects.filter(conversation=conversation, direction="in", created_at__gt=after).filter(
		(Q(has_attachments=False) & ~Q(text="")) | Q(meta__photo_reply=True)
	).exists()


@shared_task(bind=True, max_retries=2, default_retry_delay=5, soft_time_limit=60, time_limit=90)
def process_reply(self, conversation_id: str, message_id: str) -> None:
	conversation = Conversation.objects.select_related("project", "contact", "knowledge_base").get(id=conversation_id)
	msg = Message.objects.get(id=message_id)

	logger.info(f"process_reply: start conversation={conversation_id} message={message_id} "
		f"channel={conversation.channel} attempt={self.request.retries + 1}")
	# Batching: a newer message that gets its own reply task will answer the whole burst
	if _newer_text_message(conversation, msg.created_at):
		logger.info(f"process_reply: skip message={message_id}, a newer message has its own reply task (batched)")
		_complete([msg])
		return

	# One reply task per conversation at a time, or two could answer the same messages
	lock = f"reply-lock:{conversation_id}"
	if not cache.add(lock, 1, timeout=REPLY_LOCK_SECONDS):
		logger.info(f"process_reply: conversation={conversation_id} busy (lock held), retrying in 3s")
		process_reply.apply_async(args=[conversation_id, message_id], countdown=3)
		return
	try:
		_reply(self, conversation, msg)
	finally:
		cache.delete(lock)
		logger.info(f"process_reply: done conversation={conversation_id} message={message_id}")


def _reply(task, conversation, msg: Message) -> None:
	turn = pending_turn(conversation)
	if not turn:
		logger.info(f"process_reply: conversation={conversation.id} nothing pending, already answered")
		_complete([msg])  # already covered by an earlier reply
		return
	logger.info(f"process_reply: conversation={conversation.id} turn={len(turn)} inbound message(s) "
		f"{[str(m.id) for m in turn]}")
	if conversation.is_paused():
		logger.info(f"process_reply: conversation={conversation.id} paused (staff handling), not replying")
		_complete(turn)  # staff answer these
		return

	out_of_credits = False
	if conversation.channel != "self":
		try:
			cost = price_of("reply") + (price_of("photo_search") if any((m.meta or {}).get("photo_reply") for m in turn) else 0)
			out_of_credits = shopper_blocked(conversation.project, cost)
		except Exception:  # fail open: a billing error never stops a shopper reply
			logger.exception("Out-of-credits check failed for conversation %s", conversation.id)
			out_of_credits = False

	try:
		if not out_of_credits:
			image_reply.annotate_turn(conversation, turn)
		photos_timed_out = False
	except SoftTimeLimitExceeded:
		# Too little time left for the agent before the hard kill: send the fallback now
		logger.warning(f"Out of time checking photos for conversation {conversation.id}; sending the fallback reply")
		photos_timed_out = True
	logger.info(f"process_reply: conversation={conversation.id} photos_timed_out={photos_timed_out}")
	question = "\n".join(tasks.format_message_with_context(m) for m in turn)
	api_calls: list = []
	trace: dict = {}
	handoff_reason = None
	agent_replied = False
	if out_of_credits:
		logger.info(f"process_reply: conversation={conversation.id} project out of credits, handing off without the agent")
		reply_text = ""
		handoff_reason = "out of credits"
	elif any(wants_human(m.text) for m in turn):  # raw text only: quoted bot messages must not trigger it
		logger.info(f"process_reply: conversation={conversation.id} customer asked for a person, handing off without the agent")
		reply_text = ""
		handoff_reason = "customer asked for a person"
	elif photos_timed_out:
		logger.info(f"process_reply: conversation={conversation.id} no agent run (photo check timed out)")
		reply_text = ""
	else:
		logger.info(f"process_reply: conversation={conversation.id} calling agent, question chars={len(question)}")
		try:
			reply_text, ctx = tasks.generate_agent_reply(turn[0], conversation.knowledge_base, question=question)
			api_calls, handoff_reason, trace = ctx.api_call_log, ctx.handoff_reason, ctx.trace
			agent_replied = bool(reply_text)
			logger.info(
				f"process_reply: conversation={conversation.id} agent answered: reply chars={len(reply_text or '')}, "
				f"api_calls={len(api_calls)}, handoff={handoff_reason!r}"
			)
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
			logger.error(
				f"Reply generation failed for conversation {conversation.id}: {exc} "
				f"(timed_out={timed_out}, sent_post={sent_post}, fallback reply used)",
				exc_info=True,
			)
			reply_text = ""

	# A message that landed mid-generation has its own task waiting on the lock, and it answers this turn too.
	# Except after a POST: dropping that reply would hide what was done (order, booking) from the customer.
	if _newer_text_message(conversation, turn[-1].created_at) and not any(c.get("method") == "POST" for c in api_calls):
		logger.info(f"process_reply: conversation={conversation.id} dropping reply, a newer message arrived during generation")
		return

	# Staff may have taken over while the bot was generating: their reply wins, the bot's is dropped
	conversation.refresh_from_db(fields=["handoff_at", "handoff_reason"])
	if conversation.is_paused():
		logger.info(f"process_reply: conversation={conversation.id} staff took over during generation, reply dropped")
		_complete(turn)
		return
	# ponytail: canned texts in English + Bengali only (script check); Banglish gets English. Add languages here if needed.
	bengali = any("ঀ" <= ch <= "৿" for m in turn for ch in (m.text or ""))
	if handoff_reason:
		if not start_handoff(conversation, handoff_reason):
			logger.info(f"process_reply: conversation={conversation.id} handoff skipped, already paused")
			_complete(turn)  # another task or staff paused it first; don't send a second handoff message
			return
		logger.info(f"process_reply: conversation={conversation.id} handoff started: {handoff_reason!r}")
		reply_text = settings.HANDOFF_MESSAGE_BN if bengali else settings.HANDOFF_MESSAGE
	reply_text = reply_text or (tasks.FALLBACK_REPLY_BN if bengali else tasks.FALLBACK_REPLY)

	newest = turn[-1]
	local = conversation.channel in ("self", "widget")  # no provider: the stored row is the delivery (see SENDERS)
	billable = agent_replied and not handoff_reason and conversation.channel != "self"
	# The reply is saved "pending" in the same transaction that completes the inbound, so a crash after this commit
	# leaves a row recover_pending_deliveries re-sends; only the send is ever retried, never the LLM.
	with transaction.atomic():
		out_msg = create_outbound_message(
			project=conversation.project,
			contact=conversation.contact,
			text=reply_text,
			meta={
				**answered_marker(newest),
				**({"api_calls": api_calls} if api_calls else {}),
				**({"trace": trace} if trace else {}),
				**({"billable": "reply", "trigger_id": str(newest.id)} if billable else {}),
			},
			conversation=conversation,
			id=newest.agent_reply_id or None,
			status="completed" if local else "pending",
		)
		_complete(turn)
	schedule_memory_update(conversation)
	if local:
		charge_delivered_reply(out_msg)
		return
	# Sent by its own task, not here: this task's 60s soft limit could cut a send off and leave it unknown.
	# If this enqueue is lost, recover_pending_deliveries finds the pending row.
	out_id = str(out_msg.id)
	transaction.on_commit(lambda: deliver_outbound.delay(out_id))


MAX_DELIVERY_ATTEMPTS = 5
DELIVERY_LEASE_SECONDS = 120  # a worker that dies mid-send frees the reply after this
DELIVERY_BACKOFF_SECONDS = 30  # doubles per failed attempt, capped at 10 minutes


def attempt_delivery(message_id) -> None:
	"""Claim and send one saved outbound reply, then record the outcome. Safe to call any number of times from
	any worker: only one caller wins the claim, and a completed reply is never sent again.
	Meta's send APIs take no idempotency key, so a send whose outcome was not recorded (worker died mid-request,
	timeout) is held as "unknown" instead of retried; confirmed failures are retried with backoff."""
	now = timezone.now()
	claimed = Message.objects.filter(
		Q(status="pending", deliver_after__lte=now) | Q(status="processing", deliver_after__lt=now),
		id=message_id, direction="out", conversation__isnull=False,
	).update(
		status="processing", deliver_after=now + timedelta(seconds=DELIVERY_LEASE_SECONDS),
		delivery_attempts=F("delivery_attempts") + 1,
	)
	if not claimed:
		return
	out_msg = Message.objects.select_related("conversation__contact").get(id=message_id)
	conversation = out_msg.conversation
	if out_msg.delivery_attempts > MAX_DELIVERY_ATTEMPTS:  # attempts that killed their worker count too
		logger.error(f"Reply {out_msg.id} abandoned after {MAX_DELIVERY_ATTEMPTS} delivery attempts")
		out_msg.status = "failed"
		out_msg.save(update_fields=["status"])
		return
	if "inflight" in out_msg.meta:  # the attempt before this one died mid-request: never resend blindly
		return _hold_unknown(out_msg)
	newest_in = conversation.messages.filter(direction="in").order_by("-created_at").first()
	try:
		delivered = deliver(conversation, out_msg, newest_in)
	except Exception:
		logger.exception(f"Delivery of reply {out_msg.id} raised")
		delivered = False
	if not delivered and "inflight" in out_msg.meta:  # timeout or 5xx: the provider may have accepted it
		return _hold_unknown(out_msg)
	if delivered:
		logger.info(f"Reply {out_msg.id} delivered ({conversation.channel})")
		out_msg.status = "completed"
		out_msg.save(update_fields=["status"])
		charge_delivered_reply(out_msg)
		return
	if out_msg.delivery_attempts >= MAX_DELIVERY_ATTEMPTS:
		logger.error(f"Reply {out_msg.id} not delivered after {out_msg.delivery_attempts} attempts, giving up")
		out_msg.status = "failed"  # failed rows don't count as answered (pending_turn, build_history)
		out_msg.save(update_fields=["status"])
		return
	backoff = min(DELIVERY_BACKOFF_SECONDS * 2 ** (out_msg.delivery_attempts - 1), 600)
	logger.warning(f"Reply {out_msg.id} delivery attempt {out_msg.delivery_attempts} failed, retrying in {backoff}s")
	out_msg.status = "pending"
	out_msg.deliver_after = timezone.now() + timedelta(seconds=backoff)
	out_msg.save(update_fields=["status", "deliver_after"])


def _hold_unknown(out_msg: Message) -> None:
	"""Outcome of the in-flight part is unknown. It is not retried: that could message the customer twice.
	WhatsApp resolves itself through the status webhook (see resolve_unknown); otherwise staff decide in the admin."""
	logger.error(f"Reply {out_msg.id} part {out_msg.meta.get('inflight')} may or may not have been delivered; held as unknown")
	out_msg.status = "unknown"
	out_msg.deliver_after = timezone.now()  # when it was held: recover_pending_deliveries ages it from here
	out_msg.save(update_fields=["status", "deliver_after"])


def resolve_unknown(message_id, accepted: bool, part: Optional[int] = None, provider_id: str = "") -> None:
	"""Settle a reply held as unknown: the in-flight part was accepted (carry on after it) or was not (send it again).
	`part` guards against a late status for a different part than the one in flight."""
	out_msg = Message.objects.filter(id=message_id, direction="out", status="unknown").first()
	if not out_msg or (part is not None and part != out_msg.meta.get("inflight")):
		return
	meta = {k: v for k, v in out_msg.meta.items() if k not in ("inflight", "send_error")}
	done = False
	if accepted:
		meta["parts_sent"] = sent = out_msg.meta.get("inflight", 0) + 1
		done = sent >= meta.get("parts_total", 1)
		out_msg.external_message_id = provider_id or out_msg.external_message_id
	out_msg.meta = meta
	out_msg.status = "completed" if done else "pending"
	out_msg.deliver_after = timezone.now()
	out_msg.save(update_fields=["meta", "status", "deliver_after", "external_message_id"])
	logger.info(f"Reply {out_msg.id} resolved: accepted={accepted}, now {out_msg.status}")
	if done:
		charge_delivered_reply(out_msg)


@shared_task(soft_time_limit=60, time_limit=90)  # under DELIVERY_LEASE_SECONDS, so a hung send ends before its lease does
def deliver_outbound(message_id: str) -> None:
	attempt_delivery(message_id)


UNKNOWN_REVIEW_AFTER = timedelta(minutes=15)


def _age_unknown_deliveries(now) -> None:
	"""Replies held as unknown for 15+ minutes. WhatsApp reports every message it accepted through a status webhook
	within seconds, so silence this long means the send most likely never arrived: send it again (if the webhook was
	only very late the customer gets a duplicate). Messenger and Instagram have no such signal, so staff are told."""
	stale = Message.objects.filter(
		direction="out", status="unknown", deliver_after__lt=now - UNKNOWN_REVIEW_AFTER, conversation__isnull=False,
	).select_related("conversation__contact").order_by("deliver_after")[:100]
	needs_review = 0
	for out_msg in stale:
		if out_msg.conversation.channel == "whatsapp":
			resolve_unknown(out_msg.id, accepted=False)
		else:
			needs_review += 1
	if needs_review:
		logger.error(f"{needs_review} replies have been held as unknown for over 15 minutes and need review in the admin")


@shared_task
def recover_pending_deliveries() -> None:
	"""Re-send replies that were saved but not delivered: queued ones whose retry time has come, and ones whose
	worker died mid-send (lease expired). Runs every 30s; attempt_delivery's claim makes overlap harmless."""
	_age_unknown_deliveries(timezone.now())
	due = list(
		Message.objects.filter(
			direction="out", status__in=("pending", "processing"), deliver_after__lt=timezone.now(), conversation__isnull=False,
		).order_by("deliver_after").values_list("id", flat=True)[:100]
	)
	# Once per row per minute: when the queue is behind, a row stays due for many sweeps and would be queued every time
	queued = [m for m in due if cache.add(f"deliver-queued:{m}", 1, timeout=60)]
	for message_id in queued:
		deliver_outbound.delay(str(message_id))
	if queued:
		logger.warning(f"Re-queued {len(queued)} undelivered replies")


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
	"""One delayed extraction per contact per MEMORY_UPDATE_DELAY_SECONDS: it reads every message since
	memory_through, so it covers all replies made while it waited."""
	if conversation.contact_id:
		contact_id = str(conversation.contact_id)
		delay = settings.MEMORY_UPDATE_DELAY_SECONDS
		if cache.add(f"memory-update:{contact_id}", 1, timeout=delay):
			transaction.on_commit(lambda: memory.update_contact_memory.apply_async(args=[contact_id], countdown=delay))
