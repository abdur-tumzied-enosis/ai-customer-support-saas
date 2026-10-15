from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from accounts.models import Project
from channels_app.models import Conversation, CustomerContact, Message
from channels_app.reply import process_reply
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from channels_app.service.message_service import create_ui_message
from channels_app.tasks import FALLBACK_REPLY, handle_incoming_message, handle_ui_message
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
		self.addCleanup(mock.patch.stopall)

	def inbound(self, text, **extra):
		return Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text=text, **extra
		)

	def run_reply(self, msg):
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
		with mock.patch("channels_app.memory.update_contact_memory.delay") as remember:
			with self.captureOnCommitCallbacks(execute=True):
				self.run_reply(self.inbound("refund it?"))
		assert "order_number: #4471" in seen[0]
		remember.assert_called_once_with(str(self.contact.id))

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

	def test_message_arriving_during_generation_is_answered_next(self):
		late = {}

		def while_typing(question, context):
			if "late" not in late:
				late["late"] = self.inbound("second, sent while bot was typing")
			return "answer"

		seen = self.capture(while_typing)
		self.run_reply(self.inbound("first"))
		self.run_reply(late["late"])
		assert len(seen) == 2
		assert "second, sent while bot was typing" in seen[1] and "first" not in seen[1]
		late["late"].refresh_from_db()
		assert late["late"].status == "completed"

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
