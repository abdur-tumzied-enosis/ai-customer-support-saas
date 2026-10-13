import hashlib
import hmac
import json
import socket
from unittest import mock

from django.test import TestCase

from accounts.models import Project
from channels_app.models import ProjectWebhook
from channels_app.webhooks import (
	WebhookUrlError, build_event, deliver_webhook, emit_event, sign, validate_webhook_url,
)

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]


@mock.patch("agents.api_tool.socket.getaddrinfo", return_value=PUBLIC)
class WebhookTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-hooks")
		self.hook = ProjectWebhook.objects.create(project=self.project, url="https://hooks.example.com/in", is_active=True)

	def test_sign_matches_hmac_sha256(self, _dns):
		body = b'{"a":1}'
		expected = hmac.new(b"s3", body, hashlib.sha256).hexdigest()
		assert sign("s3", body) == f"sha256={expected}"

	def test_validate_rejects_private_and_bad_scheme(self, dns):
		assert validate_webhook_url(" https://hooks.example.com/x ") == "https://hooks.example.com/x"
		with self.assertRaises(WebhookUrlError):
			validate_webhook_url("ftp://hooks.example.com/x")
		dns.return_value = PRIVATE
		with self.assertRaises(WebhookUrlError):
			validate_webhook_url("https://internal.example.com/x")

	@mock.patch("channels_app.webhooks.deliver_webhook.delay")
	def test_emit_only_when_active_with_url(self, delay, _dns):
		emit_event(self.project, "conversation.handoff", {"x": 1})
		assert delay.call_count == 1
		hook_id, payload = delay.call_args.args
		assert hook_id == str(self.hook.id)
		assert payload["event"] == "conversation.handoff" and payload["data"] == {"x": 1}
		assert payload["project_id"] == str(self.project.id) and payload["id"]

		self.hook.is_active = False
		self.hook.save()
		emit_event(self.project, "conversation.handoff", {})
		assert delay.call_count == 1

	@mock.patch("channels_app.webhooks.requests.post")
	def test_delivery_signs_body_and_headers(self, post, _dns):
		post.return_value = mock.Mock(status_code=200)
		payload = build_event(self.project.id, "ping", {})
		assert deliver_webhook.apply(args=[str(self.hook.id), payload]).get() == 200
		kwargs = post.call_args.kwargs
		assert kwargs["allow_redirects"] is False and kwargs["timeout"] == 10
		assert json.loads(kwargs["data"]) == payload
		h = kwargs["headers"]
		assert h["X-AMP-Event"] == "ping" and h["X-AMP-Delivery"] == payload["id"]
		assert h["X-AMP-Signature"] == sign(self.hook.secret, kwargs["data"])

	@mock.patch("channels_app.webhooks.requests.post")
	def test_retries_on_5xx(self, post, _dns):
		post.return_value = mock.Mock(status_code=503)
		deliver_webhook.apply(args=[str(self.hook.id), build_event(self.project.id, "ping", {})])
		assert post.call_count == 4  # first try + 3 retries

	@mock.patch("channels_app.webhooks.requests.post")
	def test_no_retry_on_4xx(self, post, _dns):
		post.return_value = mock.Mock(status_code=404)
		deliver_webhook.apply(args=[str(self.hook.id), build_event(self.project.id, "ping", {})])
		assert post.call_count == 1

	@mock.patch("channels_app.webhooks.requests.post")
	def test_delivery_skipped_when_url_turns_private(self, post, dns):
		dns.return_value = PRIVATE
		result = deliver_webhook.apply(args=[str(self.hook.id), build_event(self.project.id, "ping", {})])
		assert result.get() is None
		post.assert_not_called()
