import hashlib
import hmac
import json
from django.conf import settings
from django.test import Client, TestCase, override_settings


class FacebookWebhookTest(TestCase):
	def setUp(self):
		self.client = Client()

	def test_get_verification(self):
		resp = self.client.get(
			"/webhooks/facebook/",
			{"hub.mode": "subscribe", "hub.verify_token": settings.FB_VERIFY_TOKEN, "hub.challenge": "1234"},
		)
		assert resp.status_code == 200
		assert resp.content.decode() == "1234"

	def test_signature_verification(self):
		payload = {"object": "page", "entry": [{"messaging": []}]}
		body = json.dumps(payload).encode("utf-8")
		sig = hmac.new(settings.META_APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
		resp = self.client.post(
			"/webhooks/facebook/",
			data=body,
			content_type="application/json",
			HTTP_X_HUB_SIGNATURE_256=f"sha256={sig}",
		)
		assert resp.status_code == 200


