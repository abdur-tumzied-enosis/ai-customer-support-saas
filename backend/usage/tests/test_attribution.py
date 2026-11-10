import uuid
from unittest import mock

from django.test import TestCase
from langchain_core.messages import AIMessage

from accounts.models import Project
from agents.rag import Context, _track_token_usage
from channels_app.models import Conversation, CustomerContact, Message
from channels_app.service.conversation_service import ensure_contact_conversation
from channels_app.tasks import generate_agent_reply
from knowledge.models import KnowledgeBase
from usage.models import UsageEvent
from usage.utils import track_token_usage


class AttributionTests(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-attr")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-attr")
		self.contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.msg = Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text="hi"
		)

	def test_track_token_usage_links_conversation(self):
		track_token_usage(str(self.project.id), "chat_in", 5, conversation_id=str(self.conv.id), message_id=str(self.msg.id))
		e = UsageEvent.objects.get()
		self.assertEqual((e.conversation_id, e.message_id), (self.conv.id, self.msg.id))

	def test_mismatched_message_dropped(self):
		other = ensure_contact_conversation(
			CustomerContact.objects.create(project=self.project, platform="facebook", external_id="other"), self.kb
		)
		self.assertNotEqual(other.id, self.conv.id)
		with self.assertLogs("usage.utils", level="WARNING"):
			track_token_usage(str(self.project.id), "chat_in", 5, conversation_id=str(other.id), message_id=str(self.msg.id))
		e = UsageEvent.objects.get()
		self.assertEqual(e.conversation_id, other.id)
		self.assertIsNone(e.message)

	def test_reply_usage_attributed(self):
		seen = {}

		def fake(question, context, deps=None, history=None, project_id=None):
			seen["conv"], seen["msg"] = context.conversation_id, context.message_id
			_track_token_usage(project_id, question, "ok", 3, 2, [AIMessage(content="ok")], context)
			return "ok"

		with mock.patch("channels_app.tasks.generate_reply", side_effect=fake):
			generate_agent_reply(self.msg, self.kb)
		self.assertEqual(seen, {"conv": str(self.conv.id), "msg": str(self.msg.id)})
		out = UsageEvent.objects.get(kind="chat_out")
		self.assertEqual((out.conversation_id, out.message_id), (self.conv.id, self.msg.id))
