from unittest import mock

from django.test import SimpleTestCase, TestCase

from accounts.models import Project
from channels_app.facebook_client import INSTAGRAM_MAX_TEXT_BYTES, split_text
from channels_app.models import ChannelCredential, CustomerContact, Message
from channels_app.reply import deliver
from channels_app.service.facebook_messing import handle_facebook_message
from channels_app.service.message_service import create_outbound_message
from knowledge.models import KnowledgeBase


def payload(mid, text="hi", obj="instagram", sender="igsid-1", recipient="ig-1", **message):
	return {"object": obj, "entry": [{"id": recipient, "messaging": [{
		"sender": {"id": sender}, "recipient": {"id": recipient}, "timestamp": 1,
		"message": {"mid": mid, "text": text, **message},
	}]}]}


@mock.patch("channels_app.service.facebook_messing.schedule_reply")
@mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="ann.lee")
class InstagramTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-ig")
		kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-ig")
		ChannelCredential.objects.create(  # another page, connected for Messenger only
			project=self.project, channel="facebook", knowledge_base=kb,
			credentials={"page_id": "page-2", "page_access_token": "fb-tok"},
		)
		ChannelCredential.objects.create(
			project=self.project, channel="instagram", knowledge_base=kb,
			credentials={"instagram_account_id": "ig-1", "page_id": "page-1", "page_access_token": "ig-tok"},
		)

	def test_dm_creates_instagram_conversation_and_schedules_reply(self, get_name, schedule):
		handle_facebook_message(payload("m1"))
		contact = CustomerContact.objects.get(external_id="igsid-1")
		assert contact.platform == "instagram" and contact.conversation.channel == "instagram"
		assert contact.conversation.name == "ann.lee"
		get_name.assert_called_once_with("ig-tok", "igsid-1", fields="name,username")
		assert Message.objects.get(external_message_id="m1").conversation_id == contact.conversation_id
		schedule.assert_called_once()

	def test_echo_of_our_own_message_is_ignored(self, get_name, schedule):
		handle_facebook_message(payload("m1", is_echo=True))
		assert not Message.objects.exists() and not CustomerContact.objects.exists()
		schedule.assert_not_called()

	@mock.patch("channels_app.tasks.send_text_message", return_value={"message_id": "out"})
	def test_reply_uses_instagram_token_and_byte_limit(self, send, get_name, schedule):
		handle_facebook_message(payload("m1"))
		inbound = Message.objects.get(external_message_id="m1")
		conv = inbound.conversation
		out = create_outbound_message(
			project=self.project, contact=conv.contact, text="আমাদের দোকান খোলা। " * 120, conversation=conv,
		)
		assert deliver(conv, out, inbound)
		assert send.call_count > 1
		for call in send.call_args_list:
			assert call.kwargs["page_access_token"] == "ig-tok" and call.kwargs["recipient_psid"] == "igsid-1"
			assert len(call.kwargs["text"].encode()) <= INSTAGRAM_MAX_TEXT_BYTES

	@mock.patch("channels_app.tasks.send_text_message", return_value={"message_id": "out"})
	def test_linked_page_messenger_message_is_answered_with_the_instagram_connection(self, send, get_name, schedule):
		handle_facebook_message(payload("m1", obj="page", sender="psid-1", recipient="page-1"))
		inbound = Message.objects.get(external_message_id="m1")
		conv = inbound.conversation
		assert conv.channel == "facebook"
		get_name.assert_called_once_with("ig-tok", "psid-1")
		out = create_outbound_message(project=self.project, contact=conv.contact, text="Hello", conversation=conv)
		assert deliver(conv, out, inbound)
		assert send.call_args.kwargs["page_access_token"] == "ig-tok"


class SplitTextBytesTest(SimpleTestCase):
	def test_parts_fit_byte_limit_without_losing_words(self):
		text = "দোকান খোলা। " * 200
		parts = split_text(text, max_bytes=100)
		assert all(len(p.encode()) <= 100 for p in parts)
		assert " ".join(parts).split() == text.split()  # nothing lost or cut mid-word
		assert split_text("short", max_bytes=100) == ["short"]
