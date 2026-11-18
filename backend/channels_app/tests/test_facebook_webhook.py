import hashlib
import hmac
import json
from unittest import mock

from django.conf import settings
from django.test import Client, TestCase, override_settings

from accounts.models import Project
from channels_app.models import ChannelCredential, Message
from channels_app.service.facebook_messing import handle_facebook_message
from knowledge.models import KnowledgeBase


class FacebookWebhookTest(TestCase):
	def setUp(self):
		self.client = Client()

	def test_get_verification(self):
		project = Project.objects.create(name="P", slug="p-fb-verify")
		ChannelCredential.objects.create(project=project, channel="facebook", credentials={"verify_token": "fb-verify"})
		resp = self.client.get(
			"/webhooks/meta/",
			{"hub.mode": "subscribe", "hub.verify_token": "fb-verify", "hub.challenge": "1234"},
		)
		assert resp.status_code == 200
		assert resp.content.decode() == "1234"

	def test_signature_verification(self):
		payload = {"object": "page", "entry": [{"messaging": []}]}
		body = json.dumps(payload).encode("utf-8")
		sig = hmac.new(settings.META_APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
		resp = self.client.post(
			"/webhooks/meta/",
			data=body,
			content_type="application/json",
			HTTP_X_HUB_SIGNATURE_256=f"sha256={sig}",
		)
		assert resp.status_code == 200

	@override_settings(META_VERIFY_TOKEN="global-vt")
	def test_global_verify_token_matches(self):
		resp = self.client.get("/webhooks/meta/", {
			"hub.mode": "subscribe", "hub.verify_token": "global-vt", "hub.challenge": "9999",
		})
		assert resp.status_code == 200 and resp.content.decode() == "9999"

	@override_settings(META_VERIFY_TOKEN="global-vt")
	def test_non_ascii_verify_token_is_rejected_not_a_500(self):
		resp = self.client.get("/webhooks/meta/", {
			"hub.mode": "subscribe", "hub.verify_token": "café", "hub.challenge": "1",
		})
		assert resp.status_code == 403


@mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="Ann")
@mock.patch("channels_app.service.facebook_messing.store_message_media.delay")
@mock.patch("channels_app.service.facebook_messing.schedule_reply")
class VoiceNoteIntakeTest(TestCase):
	def setUp(self):
		project = Project.objects.create(name="P", slug="p-fb-voice")
		kb = KnowledgeBase.objects.create(project=project, name="kb", vector_collection="vc-fb-voice")
		for channel, creds in (
			("facebook", {"page_id": "page-1", "page_access_token": "t"}),
			("instagram", {"instagram_account_id": "ig-1", "page_id": "page-9", "page_access_token": "t"}),
		):
			ChannelCredential.objects.create(project=project, channel=channel, knowledge_base=kb, credentials=creds)

	def send(self, obj, recipient):
		handle_facebook_message({"object": obj, "entry": [{"id": recipient, "messaging": [{
			"sender": {"id": "u-1"}, "recipient": {"id": recipient}, "timestamp": 1,
			"message": {"mid": "m-voice", "attachments": [{"type": "audio", "payload": {"url": "https://x/a.mp4"}}]},
		}]}]})
		return Message.objects.get(external_message_id="m-voice")

	def check_on(self, obj, recipient, schedule, store):
		msg = self.send(obj, recipient)
		assert msg.status == "pending" and msg.meta["audio_reply"] is True
		store.assert_called_once_with(str(msg.id), True)
		schedule.assert_not_called()

	@override_settings(TRANSCRIPTION_ENABLED=True)
	def test_messenger_audio_requests_reply(self, schedule, store, name):
		self.check_on("page", "page-1", schedule, store)

	@override_settings(TRANSCRIPTION_ENABLED=True)
	def test_instagram_audio_requests_reply(self, schedule, store, name):
		self.check_on("instagram", "ig-1", schedule, store)

	def test_messenger_audio_unchanged_when_transcription_off(self, schedule, store, name):
		msg = self.send("page", "page-1")
		assert msg.status == "completed" and "audio_reply" not in msg.meta
		store.assert_called_once_with(str(msg.id), False)
		schedule.assert_not_called()
