import base64
import hashlib
import hmac
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import ChannelCredential

TEST_KEY = "DdZ_qWzIlhW-IcbMX1pHYAAqQNYv9h9rcyfDRCeRwPY="


def signed(user_id, secret="sec"):
	payload = base64.urlsafe_b64encode(json.dumps({"user_id": user_id}).encode()).rstrip(b"=")
	sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).rstrip(b"=")
	return (sig + b"." + payload).decode()


@override_settings(TOKEN_ENCRYPTION_KEY=TEST_KEY, META_APP_SECRET="sec")
class MetaDeauthorizeTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="d@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-deauth")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.client = APIClient()
		self.own = ChannelCredential.objects.create(project=self.project, channel="facebook", is_active=True,
			credentials={"page_id": "p1", "page_access_token": "enc:v1:x", "connected_via": "oauth", "meta_user_id": "u1"})
		self.other = ChannelCredential.objects.create(project=self.project, channel="facebook", is_active=True,
			credentials={"page_id": "p2", "page_access_token": "enc:v1:y", "connected_via": "oauth", "meta_user_id": "u2"})

	def test_bad_signature_changes_nothing(self):
		res = self.client.post("/api/channels/meta/deauthorize", {"signed_request": signed("u1", "wrong")})
		assert res.status_code == 400 and res.data["error"]["code"] == "invalid_signature"
		self.own.refresh_from_db()
		assert self.own.is_active and self.own.credentials["page_access_token"] == "enc:v1:x"

	def test_good_signature_deactivates_only_that_user(self):
		res = self.client.post("/api/channels/meta/deauthorize", {"signed_request": signed("u1")})
		assert res.status_code == 200 and res.data["success"] is True
		self.own.refresh_from_db()
		self.other.refresh_from_db()
		assert not self.own.is_active and self.own.credentials["page_access_token"] == ""
		assert self.other.is_active and self.other.credentials["page_access_token"] == "enc:v1:y"

	def test_signature_without_user_id_changes_nothing(self):
		payload = base64.urlsafe_b64encode(json.dumps({"algorithm": "HMAC-SHA256"}).encode()).rstrip(b"=")
		sig = base64.urlsafe_b64encode(hmac.new(b"sec", payload, hashlib.sha256).digest()).rstrip(b"=")
		res = self.client.post("/api/channels/meta/deauthorize",
			{"signed_request": (sig + b"." + payload).decode()})
		assert res.status_code == 400 and res.data["error"]["code"] == "invalid_signature"
		self.own.refresh_from_db()
		assert self.own.is_active
