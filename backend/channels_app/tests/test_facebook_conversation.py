from unittest import mock

from django.test import TestCase

from accounts.models import Project
from channels_app.models import ChannelCredential, CustomerContact, Message
from channels_app.service.facebook_messing import handle_facebook_message
from knowledge.models import KnowledgeBase


def payload(mid, text="hello"):
	return {"entry": [{"messaging": [{
		"sender": {"id": "psid-1"}, "recipient": {"id": "page-1"}, "timestamp": 1,
		"message": {"mid": mid, "text": text},
	}]}]}


@mock.patch("channels_app.service.facebook_messing.schedule_reply")
class FacebookConversationTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-fb")
		kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-fb")
		ChannelCredential.objects.create(
			project=self.project, channel="facebook", knowledge_base=kb, is_active=True,
			credentials={"page_id": "page-1", "page_access_token": "tok"},
		)

	@mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="Ann Lee")
	def test_first_message_creates_named_conversation_and_schedules(self, get_name, schedule):
		handle_facebook_message(payload("m1"))
		contact = CustomerContact.objects.get(external_id="psid-1")
		assert contact.display_name == "Ann Lee"
		assert contact.conversation is not None and contact.conversation.name == "Ann Lee"
		msg = Message.objects.get(external_message_id="m1")
		assert msg.conversation_id == contact.conversation_id and msg.sender == "customer"
		schedule.assert_called_once()
		assert schedule.call_args.args[0].id == msg.id
		get_name.assert_called_once_with("tok", "psid-1")

		handle_facebook_message(payload("m2"))
		assert Message.objects.get(external_message_id="m2").conversation_id == contact.conversation_id
		get_name.assert_called_once()  # not looked up again

	@mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="")
	def test_name_lookup_failure_is_tolerated(self, _get_name, schedule):
		handle_facebook_message(payload("m1"))
		contact = CustomerContact.objects.get(external_id="psid-1")
		assert contact.display_name == "" and contact.conversation.name == "Facebook user"
		schedule.assert_called_once()
