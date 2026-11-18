from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.test import SimpleTestCase

from channels_app.meta_status import check_channel

CALLBACK = "https://example.test/webhooks/meta/"
CREDS = SimpleNamespace(
	page_id="123", page_access_token="tok", meta_app_id="app1", meta_app_secret="sec",
	instagram_account_id="ig9", verify_token="vt",
)
FB_SCOPES = ["pages_messaging", "pages_manage_metadata"]
IG_SCOPES = ["instagram_basic", "instagram_manage_messages", "pages_manage_metadata"]
GOOD_HOOK = {"callback_url": CALLBACK, "fields": ["messages"], "active": True}


def _resp(body, status=200):
	r = Mock(status_code=status)
	r.json.return_value = body
	return r


def _graph(scopes, subscribed=("messages",), valid=True, ig=None, hook=GOOD_HOOK, hook_error=None):
	"""Route fake Graph responses by URL path. `hook` is the app webhook saved in Meta, for each object."""
	def fake_get(url, params=None, timeout=None):
		if url.endswith("/debug_token"):
			return _resp({"data": {"is_valid": valid, "scopes": scopes}})
		if url.endswith("/subscriptions"):
			if hook_error:
				return _resp({"error": {"message": hook_error}}, status=400)
			data = [{"object": obj, **hook} for obj in ("page", "instagram")] if hook else []
			return _resp({"data": data})
		if url.endswith("/subscribed_apps"):
			return _resp({"data": [{"id": "app1", "subscribed_fields": list(subscribed)}]})
		return _resp({"instagram_business_account": ig} if ig is not None else {})
	return fake_get


def _status(checks, key):
	return next(c["status"] for c in checks if c["key"] == key)


def _check(channel, creds=CREDS, **graph):
	with patch("channels_app.meta_status.requests.get", side_effect=_graph(**graph)):
		return check_channel(channel, creds, CALLBACK)


class CheckChannelTests(SimpleTestCase):
	def test_all_ok_facebook(self):
		result = _check("facebook", scopes=FB_SCOPES)
		self.assertTrue(result["ok"])
		self.assertIsNone(result["error"])
		self.assertEqual(_status(result["checks"], "subscription"), "ok")
		self.assertEqual(_status(result["checks"], "app_webhook"), "ok")
		self.assertEqual(_status(result["checks"], "verify_token"), "ok")

	def test_missing_permission_is_reported(self):
		result = _check("facebook", scopes=["pages_manage_metadata"])
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "permission:pages_messaging"), "missing")
		self.assertEqual(_status(result["checks"], "permission:pages_manage_metadata"), "ok")

	def test_missing_subscription_is_reported(self):
		result = _check("facebook", scopes=FB_SCOPES, subscribed=[])
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "subscription"), "missing")

	def test_invalid_token_stops_after_token_check(self):
		result = _check("facebook", scopes=[], valid=False)
		self.assertFalse(result["ok"])
		self.assertEqual([c["key"] for c in result["checks"]], ["token"])
		self.assertEqual(result["checks"][0]["status"], "error")

	def test_graph_error_is_reported(self):
		err = _resp({"error": {"message": "Unsupported get request."}}, status=400)
		with patch("channels_app.meta_status.requests.get", return_value=err):
			result = check_channel("facebook", CREDS, CALLBACK)
		self.assertFalse(result["ok"])
		self.assertEqual(result["checks"][0]["status"], "error")
		self.assertEqual(result["checks"][0]["detail"], "Unsupported get request.")

	def test_instagram_link_mismatch_is_reported(self):
		result = _check("instagram", scopes=IG_SCOPES, ig={"id": "other"})
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "instagram_link"), "missing")

	def test_instagram_app_webhook_is_checked(self):
		result = _check("instagram", scopes=IG_SCOPES, ig={"id": "ig9"})
		self.assertTrue(result["ok"])
		self.assertEqual(_status(result["checks"], "app_webhook"), "ok")

	def test_app_webhook_not_saved_in_meta(self):
		result = _check("facebook", scopes=FB_SCOPES, hook=None)
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "app_webhook"), "missing")

	def test_app_webhook_pointing_elsewhere_is_reported(self):
		other = dict(GOOD_HOOK, callback_url="https://old.test/webhooks/meta/")
		result = _check("facebook", scopes=FB_SCOPES, hook=other)
		self.assertEqual(_status(result["checks"], "app_webhook"), "missing")
		detail = next(c["detail"] for c in result["checks"] if c["key"] == "app_webhook")
		self.assertIn("https://old.test/webhooks/meta/", detail)

	def test_app_webhook_with_messages_inactive_is_reported(self):
		result = _check("facebook", scopes=FB_SCOPES, hook=dict(GOOD_HOOK, active=False))
		self.assertEqual(_status(result["checks"], "app_webhook"), "missing")

	def test_app_webhook_error_does_not_hide_other_checks(self):
		result = _check("facebook", scopes=FB_SCOPES, hook_error="(#10) Application does not have permission")
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "app_webhook"), "error")
		self.assertEqual(_status(result["checks"], "subscription"), "ok")

	def test_missing_verify_token_is_reported(self):
		creds = SimpleNamespace(**{**vars(CREDS), "verify_token": ""})
		result = _check("facebook", creds, scopes=FB_SCOPES)
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "verify_token"), "missing")

	def test_missing_credentials_makes_no_graph_call(self):
		empty = SimpleNamespace(page_id="", page_access_token="", meta_app_id="", meta_app_secret="")
		with patch("channels_app.meta_status.requests.get") as get:
			result = check_channel("facebook", empty, CALLBACK)
		get.assert_not_called()
		self.assertFalse(result["ok"])
		self.assertIsNotNone(result["error"])


class SubscribePageTests(SimpleTestCase):
	def test_posts_messages_subscription_with_page_token(self):
		from channels_app.meta_status import subscribe_page
		with patch("channels_app.meta_status.requests.post", return_value=_resp({"success": True})) as post:
			subscribe_page(CREDS)
		kwargs = post.call_args.kwargs
		self.assertTrue(post.call_args.args[0].endswith("/123/subscribed_apps"))
		self.assertEqual(kwargs["data"], {"subscribed_fields": "messages", "access_token": "tok"})

	def test_meta_error_is_raised_with_its_message(self):
		from channels_app.meta_status import subscribe_page, GraphError
		err = _resp({"error": {"message": "(#200) Requires pages_manage_metadata permission"}}, status=400)
		with patch("channels_app.meta_status.requests.post", return_value=err):
			with self.assertRaisesMessage(GraphError, "(#200) Requires pages_manage_metadata permission"):
				subscribe_page(CREDS)
