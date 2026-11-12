import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.tasks import send_email

User = get_user_model()


class EmailFlowTests(TestCase):
	def setUp(self):
		cache.clear()
		self.client = APIClient()
		# Run the Celery task inline; mail lands in django.core.mail.outbox
		patcher = mock.patch("accounts.tasks.send_email.delay", side_effect=send_email)
		patcher.start()
		self.addCleanup(patcher.stop)

	def _signup(self):
		with self.captureOnCommitCallbacks(execute=True):
			r = self.client.post("/api/auth/signup/", {
				"first_name": "Ann", "last_name": "Lee", "email": "ann@x.com",
				"password": "testpass123", "terms_version": "2026-10-29",
			}, format="json")
		assert r.status_code == 201, r.json()
		return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

	def test_signup_code_verifies_and_signs_in(self):
		self._signup()
		assert self.client.get("/api/auth/me/").status_code == 403  # not signed in yet
		with self.captureOnCommitCallbacks(execute=True):
			login = self.client.post("/api/auth/login/", {"email": "ann@x.com", "password": "testpass123"}, format="json")
		assert login.status_code == 403 and login.json()["error"]["code"] == "email_unverified"
		code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)  # login sent a fresh one
		wrong = "000000" if code != "000000" else "111111"
		assert self.client.post("/api/auth/verify-email/", {"email": "ann@x.com", "code": wrong}, format="json").status_code == 400
		r = self.client.post("/api/auth/verify-email/", {"email": "ann@x.com", "code": code}, format="json")
		assert r.status_code == 200, r.json()
		assert User.objects.get(email="ann@x.com").email_verified
		assert self.client.get("/api/auth/me/").status_code == 200
		# Codes are single use
		assert self.client.post("/api/auth/verify-email/", {"email": "ann@x.com", "code": code}, format="json").status_code == 400

	def test_code_dropped_after_max_attempts(self):
		code = self._signup()
		wrong = "000000" if code != "000000" else "111111"
		for _ in range(5):
			self.client.post("/api/auth/verify-email/", {"email": "ann@x.com", "code": wrong}, format="json")
		assert self.client.post("/api/auth/verify-email/", {"email": "ann@x.com", "code": code}, format="json").status_code == 400

	@override_settings(EMAIL_LOGO_URL="")  # don't inherit a logo URL from .env
	def test_password_reset(self):
		user = User.objects.create_user(username="bo@x.com", email="bo@x.com", password="old-pass-123", email_verified=False)
		with self.captureOnCommitCallbacks(execute=True):
			r = self.client.post("/api/auth/password-reset/", {"email": "BO@x.com"}, format="json")
		assert r.status_code == 200
		with self.captureOnCommitCallbacks(execute=True):
			unknown = self.client.post("/api/auth/password-reset/", {"email": "nobody@x.com"}, format="json")
		assert unknown.json()["message"] == r.json()["message"] and len(mail.outbox) == 1
		uid, token = re.search(r"uid=([^&]+)&token=(\S+)", mail.outbox[0].body).groups()
		html, _ = mail.outbox[0].alternatives[0]
		assert "<strong>Tumar</strong>Agent" in html and '<a href="' in html and f"reset-password?uid={uid}" in html
		with self.settings(EMAIL_LOGO_URL="https://cdn.x/logo.png"):
			send_email("bo@x.com", "s", "b")
		html, _ = mail.outbox[-1].alternatives[0]
		assert '<img src="https://cdn.x/logo.png" alt="TumarAgent"' in html and "<strong>Tumar</strong>" not in html
		bad = self.client.post("/api/auth/password-reset/confirm/", {"uid": uid, "token": "nope", "new_password": "new-pass-456"}, format="json")
		assert bad.status_code == 400
		ok = self.client.post("/api/auth/password-reset/confirm/", {"uid": uid, "token": token, "new_password": "new-pass-456"}, format="json")
		assert ok.status_code == 200, ok.json()
		user.refresh_from_db()
		assert user.check_password("new-pass-456") and user.email_verified
		# Link is spent once the password changed
		again = self.client.post("/api/auth/password-reset/confirm/", {"uid": uid, "token": token, "new_password": "other-pass-789"}, format="json")
		assert again.status_code == 400
