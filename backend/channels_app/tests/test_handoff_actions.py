from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from accounts.models import Project
from channels_app.handoff import end_handoff, note_staff_activity, start_handoff
from channels_app.models import Conversation, CustomerContact, Message
from knowledge.models import KnowledgeBase


@mock.patch("channels_app.handoff.emit_event")
class HandoffActionsTest(TestCase):
	def setUp(self):
		project = Project.objects.create(name="P", slug="p-handoff")
		kb = KnowledgeBase.objects.create(project=project, name="kb", vector_collection="vc-handoff")
		contact = CustomerContact.objects.create(project=project, platform="facebook", external_id="psid", display_name="Ann")
		self.conv = Conversation.objects.create(project=project, contact=contact, knowledge_base=kb)

	def test_start_and_end_emit_events(self, emit):
		start_handoff(self.conv, "refund request")
		self.conv.refresh_from_db()
		assert self.conv.is_paused() and self.conv.handoff_reason == "refund request"
		event, data = emit.call_args.args[1:]
		assert event == "conversation.handoff"
		assert data["reason"] == "refund request" and data["channel"] == "facebook"
		assert data["contact"]["display_name"] == "Ann"

		end_handoff(self.conv)
		self.conv.refresh_from_db()
		assert not self.conv.is_paused() and self.conv.handoff_reason == ""
		assert emit.call_args.args[1] == "conversation.resumed"

	def test_staff_activity_pauses_active_conversation(self, emit):
		note_staff_activity(self.conv)
		self.conv.refresh_from_db()
		assert self.conv.is_paused() and self.conv.handoff_reason == "staff took over"
		assert emit.call_args.args[1] == "conversation.handoff"

	def test_staff_activity_refreshes_paused_conversation(self, emit):
		self.conv.handoff_at = timezone.now() - timedelta(hours=20)
		self.conv.handoff_reason = "angry customer"
		self.conv.save()
		note_staff_activity(self.conv)
		self.conv.refresh_from_db()
		assert timezone.now() - self.conv.handoff_at < timedelta(minutes=1)
		assert self.conv.handoff_reason == "angry customer"
		emit.assert_not_called()

	def test_start_handoff_is_noop_when_already_paused(self, emit):
		assert start_handoff(self.conv, "refund request") is True
		assert start_handoff(self.conv, "paused by staff") is False
		self.conv.refresh_from_db()
		assert self.conv.handoff_reason == "refund request"
		assert emit.call_count == 1

	def inbound(self, text, photo=False):
		return Message.objects.create(
			project=self.conv.project, contact=self.conv.contact, conversation=self.conv, direction="in", text=text,
			has_attachments=photo, attachment_info=[{"type": "image", "path": "chat_media/x.webp"}] if photo else [],
		)

	def test_event_includes_last_message_and_photo(self, emit):
		msg = self.inbound("", photo=True)
		start_handoff(self.conv, "refund request")
		data = emit.call_args.args[2]
		assert data["last_message_id"] == str(msg.id) and data["has_photo"] is True
		assert data["reason"].endswith("; customer sent a photo")

	def test_photo_then_hello_keeps_photo_context(self, emit):
		self.inbound("", photo=True)
		newest = self.inbound("hello??")
		start_handoff(self.conv, "customer needs help")
		data = emit.call_args.args[2]
		assert data["last_message_id"] == str(newest.id) and data["has_photo"] is True
		assert data["reason"] == "customer needs help; customer sent a photo"

	def test_event_without_photo_has_false(self, emit):
		self.inbound("hi there")
		start_handoff(self.conv, "customer needs help")
		data = emit.call_args.args[2]
		assert data["has_photo"] is False and data["reason"] == "customer needs help"

	def test_photo_reason_not_duplicated(self, emit):
		self.inbound("", photo=True)
		start_handoff(self.conv, "customer sent a photo of a cracked lid")
		assert emit.call_args.args[2]["reason"] == "customer sent a photo of a cracked lid"

	def test_resume_event_carries_photo_context(self, emit):
		msg = self.inbound("", photo=True)
		start_handoff(self.conv, "refund request")
		end_handoff(self.conv)
		data = emit.call_args.args[2]
		assert data["last_message_id"] == str(msg.id) and data["has_photo"] is True
