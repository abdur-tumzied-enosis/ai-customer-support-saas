from datetime import timedelta
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from accounts.models import Project
from channels_app.models import Conversation, CustomerContact, Message
from channels_app.facebook_client import split_text
from channels_app import reply
from channels_app.reply import deliver_outbound, process_reply, recover_pending_deliveries, recover_stuck_replies
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from channels_app.service.message_service import create_ui_message
from channels_app.tasks import FALLBACK_REPLY, FALLBACK_REPLY_BN, handle_incoming_message, handle_ui_message
from knowledge.models import KnowledgeBase


class PipelineBase(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-pipe")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-pipe")
		self.contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.questions, self.histories = [], []
		self.reply_text = "Here you go"

		def fake_generate_reply(question, context, deps=None, history=None, project_id=None):
			self.questions.append(question)
			self.histories.append([h["content"] for h in history or []])
			return self.reply_text

		self.gen = mock.patch("channels_app.tasks.generate_reply", side_effect=fake_generate_reply).start()
		self.send = mock.patch("channels_app.tasks.send_text_message", return_value={"message_id": "mid.out"}).start()
		cred = mock.Mock()
		cred.get_credentials.return_value.page_access_token = "tok"
		mock.patch("channels_app.tasks.get_facebook_credential", return_value=cred).start()
		self.emit = mock.patch("channels_app.handoff.emit_event").start()
		# the reply is sent by its own task, queued on commit: run it right away
		mock.patch("channels_app.reply.deliver_outbound.delay", side_effect=lambda i: deliver_outbound.apply(args=[i])).start()
		self.addCleanup(mock.patch.stopall)

	def inbound(self, text, **extra):
		return Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text=text, **extra
		)

	def run_reply(self, msg):
		process_reply.apply(args=[str(msg.conversation_id), str(msg.id)])
		for out in Message.objects.filter(conversation_id=msg.conversation_id, direction="out", status="pending"):
			deliver_outbound.apply(args=[str(out.id)])  # what the on-commit enqueue does in production

	def save_reply_only(self, msg):
		process_reply.apply(args=[str(msg.conversation_id), str(msg.id)])

	def outbound(self):
		return list(Message.objects.filter(conversation=self.conv, direction="out").order_by("created_at"))


class BatchingTest(PipelineBase):
	def test_older_message_task_skips_when_newer_exists(self):
		a = self.inbound("hi")
		self.inbound("price of X?")
		self.run_reply(a)
		assert self.gen.call_count == 0
		a.refresh_from_db()
		assert a.status == "completed"
		assert self.outbound() == []

	def test_newest_task_answers_whole_burst_once(self):
		a = self.inbound("hi")
		b = self.inbound("price of X?")
		self.run_reply(b)
		assert self.gen.call_count == 1
		assert "hi" in self.questions[0] and "price of X?" in self.questions[0]
		out = self.outbound()
		assert len(out) == 1 and out[0].text == "Here you go" and out[0].sender == "bot"
		assert {m.status for m in Message.objects.filter(id__in=[a.id, b.id])} == {"completed"}
		self.send.assert_called_once()
		out[0].refresh_from_db()
		assert out[0].external_message_id == "mid.out"


class HandoffPipelineTest(PipelineBase):
	def test_paused_conversation_gets_no_reply(self):
		self.conv.handoff_at = timezone.now()
		self.conv.save()
		m = self.inbound("anyone?")
		self.run_reply(m)
		assert self.gen.call_count == 0 and self.outbound() == []
		self.send.assert_not_called()
		m.refresh_from_db()
		assert m.status == "completed"

	def test_messages_during_pause_are_answered_after_resume(self):
		self.conv.handoff_at = timezone.now() - timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS + 1)
		self.conv.save()  # expired pause
		self.inbound("first while paused", status="completed")
		m = self.inbound("still there?")
		self.run_reply(m)
		assert "first while paused" in self.questions[0] and "still there?" in self.questions[0]

	def test_keyword_rule_hands_off_without_llm(self):
		m = self.inbound("can I talk to a person")
		self.run_reply(m)
		assert self.gen.call_count == 0
		self.conv.refresh_from_db()
		assert self.conv.is_paused() and self.conv.handoff_reason == "customer asked for a person"
		assert self.outbound()[0].text == settings.HANDOFF_MESSAGE
		assert self.emit.call_args.args[1] == "conversation.handoff"
		self.send.assert_called_once()

	def test_agent_tool_handoff_replaces_reply(self):
		def tool_handoff(question, context, *args, **kwargs):
			context.handoff_reason = "refund request"
			return "Sure, I'll process your refund"

		self.gen.side_effect = tool_handoff
		self.run_reply(self.inbound("I want my money back"))
		self.conv.refresh_from_db()
		assert self.conv.handoff_reason == "refund request"
		assert self.outbound()[0].text == settings.HANDOFF_MESSAGE

	def test_bengali_customer_gets_bengali_canned_texts(self):
		self.gen.side_effect = lambda question, context, *a, **k: setattr(context, "handoff_reason", "refund") or "x"
		self.run_reply(self.inbound("আমার টাকা ফেরত চাই"))
		assert self.outbound()[0].text == settings.HANDOFF_MESSAGE_BN
		self.conv.handoff_at = None
		self.conv.save()
		self.gen.side_effect = None
		self.gen.return_value = ""  # empty reply → fallback
		self.run_reply(self.inbound("দাম কত?"))
		assert self.outbound()[-1].text == FALLBACK_REPLY_BN


class HistoryAndRetryTest(PipelineBase):
	def test_history_includes_legacy_messages_without_conversation(self):
		Message.objects.create(project=self.project, contact=self.contact, conversation=None, direction="in", text="old question")
		self.run_reply(self.inbound("new question"))
		assert "old question" in self.histories[0]

	def test_failed_reply_after_post_is_not_retried_and_calls_are_logged(self):
		calls = [{"method": "POST", "url": "https://shop.com/book", "status": 201, "ms": 5}]

		def post_then_fail(question, context, *args, **kwargs):
			context.api_call_log.extend(calls)
			raise RuntimeError("llm down")

		self.gen.side_effect = post_then_fail
		self.run_reply(self.inbound("book it"))
		assert self.gen.call_count == 1
		out = self.outbound()[0]
		assert out.text == FALLBACK_REPLY and out.meta["api_calls"] == calls

	def test_reply_trace_is_stored_on_outbound(self):
		def traced(question, context, *args, **kwargs):
			context.trace.update(model="m1", ms=42, sources=["faq.pdf"])
			return "Answer"

		self.gen.side_effect = traced
		self.run_reply(self.inbound("q"))
		assert self.outbound()[0].meta["trace"] == {"model": "m1", "ms": 42, "sources": ["faq.pdf"]}

	def test_contact_memory_reaches_prompt_and_update_is_scheduled(self):
		self.contact.memory = [{"key": "order_number", "value": "#4471", "source_msg_id": "m", "updated_at": "t"}]
		self.contact.save()
		seen = []
		self.gen.side_effect = lambda question, context, *a, **k: seen.append(context.memory_prompt) or "ok"
		with mock.patch("channels_app.memory.update_contact_memory.apply_async") as remember:
			with self.captureOnCommitCallbacks(execute=True):
				self.run_reply(self.inbound("refund it?"))
		assert "order_number: #4471" in seen[0]
		remember.assert_called_once_with(args=[str(self.contact.id)], countdown=settings.MEMORY_UPDATE_DELAY_SECONDS)

	def test_burst_of_replies_schedules_one_memory_update(self):
		with mock.patch("channels_app.memory.update_contact_memory.apply_async") as remember:
			for text in ("hi", "price?", "thanks"):
				with self.captureOnCommitCallbacks(execute=True):
					self.run_reply(self.inbound(text))
		assert len(self.outbound()) == 3
		remember.assert_called_once()

	def test_failed_reply_trace_records_error(self):
		def fail(question, context, *args, **kwargs):
			context.trace["sources"] = ["faq.pdf"]
			context.api_call_log.append({"method": "POST", "url": "https://shop.com/x"})  # blocks retry
			raise RuntimeError("llm down")

		self.gen.side_effect = fail
		self.run_reply(self.inbound("q"))
		assert self.outbound()[0].meta["trace"] == {"sources": ["faq.pdf"], "error": "RuntimeError"}


class SelfChannelTest(PipelineBase):
	def test_ui_message_reply_uses_agent_reply_id_and_no_send(self):
		conv = create_conversation(project=self.project, knowledge_base=self.kb, user_id="u1")
		msg = create_ui_message(conversation=conv, text="hello")
		process_reply.apply(args=[str(conv.id), str(msg.id)])
		out = Message.objects.get(conversation=conv, direction="out")
		assert out.id == msg.agent_reply_id
		self.send.assert_not_called()


class LegacyWrapperTest(PipelineBase):
	@mock.patch("channels_app.reply.process_reply.delay")
	def test_legacy_incoming_wrapper_creates_conversation(self, delay):
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid2")
		msg = Message.objects.create(project=self.project, contact=contact, direction="in", text="hi")
		handle_incoming_message.apply(args=[str(msg.id), str(self.kb.id)])
		msg.refresh_from_db()
		assert msg.conversation_id is not None
		delay.assert_called_once_with(str(msg.conversation_id), str(msg.id))

	@mock.patch("channels_app.reply.process_reply.delay")
	def test_legacy_ui_wrapper_enqueues(self, delay):
		m = self.inbound("hi")
		handle_ui_message.apply(args=[str(m.id)])
		delay.assert_called_once_with(str(self.conv.id), str(m.id))


class ReviewFixesTest(PipelineBase):
	def capture(self, side=None):
		seen = []

		def gen(question, context, *args, **kwargs):
			seen.append(question)
			return side(question, context) if side else "answer"

		self.gen.side_effect = gen
		return seen

	def test_message_arriving_during_generation_is_answered_with_the_turn(self):
		late = {}

		def while_typing(question, context):
			if "late" not in late:
				late["late"] = self.inbound("second, sent while bot was typing")
			return "answer"

		seen = self.capture(while_typing)
		first = self.inbound("first")
		self.run_reply(first)
		assert self.outbound() == []  # dropped: the newer message's task answers both
		self.run_reply(late["late"])
		assert len(seen) == 2
		assert "second, sent while bot was typing" in seen[1] and "first" in seen[1]
		assert len(self.outbound()) == 1
		assert {m.status for m in Message.objects.filter(id__in=[first.id, late["late"].id])} == {"completed"}

	def test_reply_after_post_is_sent_even_if_newer_message_arrived(self):
		def post_while_typing(question, context):
			context.api_call_log.append({"method": "POST", "url": "https://shop.com/book"})
			self.inbound("also, can I pay cash?")
			return "Booked for 3pm"

		self.capture(post_while_typing)
		self.run_reply(self.inbound("book 3pm"))
		assert [m.text for m in self.outbound()] == ["Booked for 3pm"]

	def test_busy_conversation_reschedules_instead_of_running(self):
		m = self.inbound("hi")
		cache.add(f"reply-lock:{self.conv.id}", 1)
		self.addCleanup(cache.delete, f"reply-lock:{self.conv.id}")
		with mock.patch("channels_app.reply.process_reply.apply_async") as later:
			self.run_reply(m)
		assert self.gen.call_count == 0
		later.assert_called_once_with(args=[str(self.conv.id), str(m.id)], countdown=3)

	def test_lock_released_after_reply(self):
		self.run_reply(self.inbound("hi"))
		assert cache.add(f"reply-lock:{self.conv.id}", 1)
		cache.delete(f"reply-lock:{self.conv.id}")


	def test_text_then_attachment_burst_gets_one_reply(self):
		seen = self.capture()
		a = self.inbound("here's a photo of the broken item")
		self.inbound("", has_attachments=True, attachment_info=[{"type": "image", "url": "u", "payload": {}}])
		self.run_reply(a)
		assert len(seen) == 1 and "image" in seen[0]

	def test_bot_reply_dropped_if_staff_paused_during_generation(self):
		def staff_pauses(question, context):
			Conversation.objects.filter(id=self.conv.id).update(handoff_at=timezone.now(), handoff_reason="paused by staff")
			return "late bot answer"

		self.capture(staff_pauses)
		m = self.inbound("question")
		self.run_reply(m)
		assert self.outbound() == []
		self.send.assert_not_called()
		m.refresh_from_db()
		assert m.status == "completed"

	def test_tool_handoff_on_already_paused_conversation_does_not_duplicate(self):
		def pause_then_tool(question, context):
			Conversation.objects.filter(id=self.conv.id).update(handoff_at=timezone.now(), handoff_reason="paused by staff")
			context.handoff_reason = "refund"
			return "x"

		self.capture(pause_then_tool)
		self.run_reply(self.inbound("refund please"))
		self.conv.refresh_from_db()
		assert self.conv.handoff_reason == "paused by staff"
		assert self.outbound() == []
		self.emit.assert_not_called()

	def test_keyword_rule_ignores_quoted_bot_text(self):
		seen = self.capture()
		bot = Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="out", sender="bot",
			text="Would you like to talk to a person?",
		)
		self.run_reply(self.inbound("no thanks", reply_to_message=bot, is_reply=True))
		assert len(seen) == 1
		self.conv.refresh_from_db()
		assert not self.conv.is_paused()

	def test_failed_staff_reply_does_not_count_as_answered(self):
		seen = self.capture()
		a = self.inbound("are you there?")
		Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="out", sender="staff",
			status="failed", text="never delivered",
		)
		self.run_reply(a)
		assert len(seen) == 1 and "are you there?" in seen[0]


class DeliveryTest(PipelineBase):
	def make_due(self, out):
		Message.objects.filter(id=out.id).update(deliver_after=timezone.now() - timedelta(seconds=1))

	def fresh(self, out):
		out.refresh_from_db()
		return out

	def test_saved_reply_is_sent_and_completed(self):
		self.run_reply(self.inbound("hi"))
		out = self.outbound()[0]
		assert (out.status, out.external_message_id, out.delivery_attempts) == ("completed", "mid.out", 1)

	def test_failed_send_stays_pending_and_retries_send_only(self):
		self.send.return_value = {}
		self.run_reply(self.inbound("hi"))
		out = self.outbound()[0]
		assert out.status == "pending" and out.delivery_attempts == 1 and out.deliver_after > timezone.now()
		assert self.gen.call_count == 1

		self.send.reset_mock()
		deliver_outbound.apply(args=[str(out.id)])  # backoff not over: nothing happens
		self.send.assert_not_called()

		self.send.return_value = {"message_id": "mid.retry"}
		self.make_due(out)
		deliver_outbound.apply(args=[str(out.id)])
		out = self.fresh(out)
		assert out.status == "completed" and out.external_message_id == "mid.retry"
		assert "send_error" not in out.meta and self.gen.call_count == 1  # send only, LLM not re-run

	def test_gives_up_after_max_attempts(self):
		self.send.return_value = {}
		self.run_reply(self.inbound("hi"))
		out = self.outbound()[0]
		for _ in range(reply.MAX_DELIVERY_ATTEMPTS - 1):
			self.make_due(out)
			deliver_outbound.apply(args=[str(out.id)])
		assert self.fresh(out).status == "failed" and self.send.call_count == reply.MAX_DELIVERY_ATTEMPTS

	def test_timeout_is_held_as_unknown_and_never_resent(self):
		self.send.return_value = {"unknown": True}  # no response: Meta may have accepted it
		self.run_reply(self.inbound("hi"))
		out = self.outbound()[0]
		assert out.status == "unknown" and out.meta["inflight"] == 0
		self.make_due(out)
		with mock.patch("channels_app.reply.deliver_outbound.delay") as delay:
			recover_pending_deliveries.apply()
		delay.assert_not_called()
		deliver_outbound.apply(args=[str(out.id)])
		assert self.send.call_count == 1 and self.fresh(out).status == "unknown"

	def test_reply_is_sent_by_its_own_task_after_commit_not_inside_the_reply_task(self):
		with mock.patch("channels_app.reply.deliver_outbound.delay") as delay:
			with self.captureOnCommitCallbacks(execute=True):
				process_reply.apply(args=[str(self.conv.id), str(self.inbound("hi").id)])
		out = self.outbound()[0]
		delay.assert_called_once_with(str(out.id))
		assert out.status == "pending" and self.send.call_count == 0  # the soft limit of process_reply can no longer cut a send

	def held_unknown(self, minutes, platform="facebook"):
		self.contact.platform = platform
		self.contact.save()
		out = Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="out", sender="bot",
			status="unknown", text="x", meta={"inflight": 0, "parts_total": 1},
		)
		Message.objects.filter(id=out.id).update(deliver_after=timezone.now() - timedelta(minutes=minutes))
		return out

	def test_stale_unknown_whatsapp_reply_is_sent_again_and_a_fresh_one_waits(self):
		stale, fresh = self.held_unknown(20, "whatsapp"), self.held_unknown(5, "whatsapp")
		with mock.patch("channels_app.reply.deliver_outbound.delay"):
			recover_pending_deliveries.apply()
		assert self.fresh(stale).status == "pending" and "inflight" not in self.fresh(stale).meta
		assert self.fresh(fresh).status == "unknown"

	def test_stale_unknown_messenger_reply_is_reported_not_resent(self):
		out = self.held_unknown(20)
		with mock.patch("channels_app.reply.deliver_outbound.delay"), self.assertLogs("channels_app.reply", "ERROR") as logs:
			recover_pending_deliveries.apply()
		assert "need review" in logs.output[0] and self.fresh(out).status == "unknown"

	def test_sweep_queues_a_due_reply_once_a_minute_even_if_it_stays_due(self):
		self.save_reply_only(self.inbound("hi"))  # the queue is behind: the row is still pending at the next sweep
		with mock.patch("channels_app.reply.deliver_outbound.delay") as delay:
			recover_pending_deliveries.apply()
			recover_pending_deliveries.apply()
		delay.assert_called_once()

	def test_rejection_is_a_confirmed_failure_and_retried(self):
		self.send.return_value = {}  # Meta answered with an error: nothing was sent
		self.run_reply(self.inbound("hi"))
		out = self.outbound()[0]
		assert out.status == "pending" and "inflight" not in out.meta

	def test_exception_during_send_is_unknown(self):
		self.send.side_effect = RuntimeError("connection dropped")
		self.run_reply(self.inbound("hi"))
		assert self.outbound()[0].status == "unknown"

	def test_unknown_reply_resolved_as_not_delivered_is_sent_again(self):
		self.send.return_value = {"unknown": True}
		self.run_reply(self.inbound("hi"))
		out = self.outbound()[0]
		reply.resolve_unknown(out.id, accepted=False)
		self.send.return_value = {"message_id": "mid.2"}
		deliver_outbound.apply(args=[str(out.id)])
		out = self.fresh(out)
		assert out.status == "completed" and out.external_message_id == "mid.2" and self.send.call_count == 2

	def test_unknown_reply_resolved_as_delivered_completes_and_charges_nothing_twice(self):
		self.send.return_value = {"unknown": True}
		self.run_reply(self.inbound("hi"))
		out = self.outbound()[0]
		with mock.patch("channels_app.reply.charge_delivered_reply") as charge:
			reply.resolve_unknown(out.id, accepted=True, provider_id="mid.9")
			reply.resolve_unknown(out.id, accepted=True, provider_id="mid.9")  # a second status webhook
		out = self.fresh(out)
		assert out.status == "completed" and out.external_message_id == "mid.9"
		charge.assert_called_once()
		assert self.send.call_count == 1

	def test_crash_after_save_is_redelivered_by_recovery(self):
		self.save_reply_only(self.inbound("hi"))  # the worker dies right after the commit, before the send is queued
		out = self.outbound()[0]
		assert out.status == "pending" and out.external_message_id == ""
		self.send.assert_not_called()
		with mock.patch("channels_app.reply.deliver_outbound.delay", side_effect=lambda i: deliver_outbound.apply(args=[i])):
			recover_pending_deliveries.apply()
		assert self.fresh(out).status == "completed"
		self.send.assert_called_once()

	def test_two_workers_send_once(self):
		self.save_reply_only(self.inbound("hi"))  # the worker dies right after the commit, before the send is queued
		out = self.outbound()[0]
		reply.attempt_delivery(out.id)
		reply.attempt_delivery(out.id)  # same task enqueued twice
		self.send.assert_called_once()

	def test_live_lease_is_not_stolen_but_expired_lease_is(self):
		self.save_reply_only(self.inbound("hi"))  # the worker dies right after the commit, before the send is queued
		out = self.outbound()[0]
		Message.objects.filter(id=out.id).update(status="processing", deliver_after=timezone.now() + timedelta(seconds=60))
		reply.attempt_delivery(out.id)
		self.send.assert_not_called()
		self.make_due(out)  # the worker that held it died
		reply.attempt_delivery(out.id)
		assert self.fresh(out).status == "completed"
		self.send.assert_called_once()

	def test_long_reply_is_split_and_resumes_after_partial_failure(self):
		self.reply_text = ("word " * 300 + "\n\n") * 2  # ~3000 chars
		self.send.side_effect = [{"message_id": "m1"}, {}]
		self.run_reply(self.inbound("tell me everything"))
		out = self.outbound()[0]
		assert out.status == "pending" and out.meta["parts_sent"] == 1
		assert all(len(c.kwargs["text"]) <= 2000 for c in self.send.call_args_list)

		self.send.side_effect = [{"message_id": "m2"}]
		self.make_due(out)
		deliver_outbound.apply(args=[str(out.id)])
		out = self.fresh(out)
		assert out.status == "completed" and out.meta["parts_sent"] == 2 and out.external_message_id == "m2"

	def crash_during_part_two(self):
		self.reply_text = ("word " * 300 + "\n\n") * 2
		self.send.side_effect = [{"message_id": "m1"}, SystemExit()]  # worker killed during part 2
		with self.assertRaises(SystemExit):
			self.run_reply(self.inbound("tell me everything"))
		out = self.outbound()[0]
		assert out.status == "processing" and out.meta["parts_sent"] == 1 and out.meta["inflight"] == 1
		self.make_due(out)  # lease expires
		return out

	def test_crash_mid_part_is_held_unknown_not_resent(self):
		out = self.crash_during_part_two()
		deliver_outbound.apply(args=[str(out.id)])
		assert self.fresh(out).status == "unknown" and self.send.call_count == 2  # part 2 may have gone out

	def test_crash_mid_part_resolved_as_not_delivered_resumes_with_that_part_only(self):
		out = self.crash_during_part_two()
		deliver_outbound.apply(args=[str(out.id)])
		reply.resolve_unknown(out.id, accepted=False)
		self.send.side_effect = [{"message_id": "m2"}]
		deliver_outbound.apply(args=[str(out.id)])
		out = self.fresh(out)
		assert out.status == "completed" and out.meta["parts_sent"] == 2 and self.send.call_count == 3

	def test_crash_mid_part_resolved_as_delivered_completes_without_sending(self):
		out = self.crash_during_part_two()
		deliver_outbound.apply(args=[str(out.id)])
		reply.resolve_unknown(out.id, accepted=True, provider_id="m2")
		out = self.fresh(out)
		assert out.status == "completed" and out.external_message_id == "m2" and self.send.call_count == 2

	def test_accepted_first_part_of_three_continues_with_the_rest(self):
		out = self.crash_during_part_two()
		Message.objects.filter(id=out.id).update(meta={**out.meta, "parts_total": 3})
		deliver_outbound.apply(args=[str(out.id)])
		reply.resolve_unknown(out.id, accepted=True)
		out = self.fresh(out)
		assert out.status == "pending" and out.meta["parts_sent"] == 2

	def test_timeout_sends_fallback_without_retry(self):
		self.gen.side_effect = SoftTimeLimitExceeded()
		self.run_reply(self.inbound("q"))
		assert self.gen.call_count == 1
		assert self.outbound()[0].text == FALLBACK_REPLY


class SplitTextTest(TestCase):
	def test_split(self):
		assert split_text("short") == ["short"]
		assert split_text("") == []
		text = "Para one. " * 100 + "\n\n" + "Para two. " * 100
		parts = split_text(text, limit=1200)
		assert parts == ["Para one. " * 99 + "Para one.", "Para two. " * 99 + "Para two."]
		assert all(len(p) <= 50 for p in split_text("x" * 120, limit=50))  # no break: hard cut


class RecoverStuckRepliesTest(PipelineBase):
	def test_requeues_newest_pending_message_per_conversation(self):
		ago = lambda minutes: timezone.now() - timedelta(minutes=minutes)
		old_a = self.inbound("a", status="pending")
		new_b = self.inbound("b", status="pending")
		ancient = self.inbound("ancient", status="pending")
		fresh = self.inbound("fresh", status="pending")
		for m, minutes in ((old_a, 20), (new_b, 10), (ancient, 300), (fresh, 1)):
			Message.objects.filter(id=m.id).update(created_at=ago(minutes))
		with mock.patch("channels_app.reply.process_reply.delay") as delay:
			recover_stuck_replies.apply()
		delay.assert_called_once_with(str(self.conv.id), str(new_b.id))
