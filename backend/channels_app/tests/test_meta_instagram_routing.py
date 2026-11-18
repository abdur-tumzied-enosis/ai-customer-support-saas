from unittest import mock

from django.test import TestCase, override_settings

from accounts.models import Project
from channels_app.credential_crypto import encrypt_credentials
from channels_app.models import ChannelCredential, Message
from channels_app.service.facebook_messing import handle_facebook_message
from knowledge.models import KnowledgeBase

TEST_KEY = "DdZ_qWzIlhW-IcbMX1pHYAAqQNYv9h9rcyfDRCeRwPY="


@override_settings(TOKEN_ENCRYPTION_KEY=TEST_KEY)
@mock.patch("channels_app.service.facebook_messing.schedule_reply")
@mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="shop")
class MetaInstagramRoutingTest(TestCase):
	def setUp(self):
		project = Project.objects.create(name="P", slug="p-meta-ig")
		kb = KnowledgeBase.objects.create(project=project, name="kb", vector_collection="vc-meta-ig")
		ChannelCredential.objects.create(project=project, channel="instagram", knowledge_base=kb,
			credentials=encrypt_credentials({"page_id": "p1", "page_access_token": "tok-p1",
				"instagram_account_id": "ig1", "meta_app_id": "1478910681051836",
				"connected_via": "oauth", "meta_user_id": "u1"}))

	def test_ig_dm_routes_to_the_oauth_instagram_row(self, get_name, schedule):
		handle_facebook_message({"object": "instagram", "entry": [{"id": "ig1", "messaging": [{
			"sender": {"id": "igs1"}, "recipient": {"id": "ig1"}, "timestamp": 1,
			"message": {"mid": "m-meta-ig", "text": "hi"},
		}]}]})
		assert Message.objects.get(external_message_id="m-meta-ig").conversation.channel == "instagram"
		get_name.assert_called_once_with("tok-p1", "igs1", fields="name,username")
		schedule.assert_called_once()
