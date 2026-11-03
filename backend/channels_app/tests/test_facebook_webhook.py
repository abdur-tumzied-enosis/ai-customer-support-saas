import hashlib
import hmac
import json
from django.conf import settings
from django.test import Client, TestCase, override_settings

from accounts.models import Project
from channels_app.models import ChannelCredential


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


