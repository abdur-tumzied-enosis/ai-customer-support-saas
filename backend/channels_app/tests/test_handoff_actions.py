from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from accounts.models import Project
from channels_app.handoff import end_handoff, note_staff_activity, start_handoff
from channels_app.models import Conversation, CustomerContact
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
