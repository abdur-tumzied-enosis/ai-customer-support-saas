from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.test import SimpleTestCase

from channels_app.meta_status import check_channel

CREDS = SimpleNamespace(
	page_id="123", page_access_token="tok", meta_app_id="app1", meta_app_secret="sec",
	instagram_account_id="ig9",
)
FB_SCOPES = ["pages_messaging", "pages_manage_metadata"]
IG_SCOPES = ["instagram_basic", "instagram_manage_messages", "pages_manage_metadata"]


def _resp(body, status=200):
	r = Mock(status_code=status)
	r.json.return_value = body
	return r


def _graph(scopes, subscribed=("messages",), valid=True, ig=None):
	"""Route fake Graph responses by URL path."""
	def fake_get(url, params=None, timeout=None):
		if url.endswith("/debug_token"):
			return _resp({"data": {"is_valid": valid, "scopes": scopes}})
		if url.endswith("/subscribed_apps"):
			return _resp({"data": [{"id": "app1", "subscribed_fields": list(subscribed)}]})
		return _resp({"instagram_business_account": ig} if ig is not None else {})
	return fake_get


def _status(checks, key):
	return next(c["status"] for c in checks if c["key"] == key)


class CheckChannelTests(SimpleTestCase):
	def test_all_ok_facebook(self):
		with patch("channels_app.meta_status.requests.get", side_effect=_graph(FB_SCOPES)):
			result = check_channel("facebook", CREDS)
		self.assertTrue(result["ok"])
		self.assertIsNone(result["error"])
		self.assertEqual(_status(result["checks"], "subscription"), "ok")

	def test_missing_permission_is_reported(self):
		with patch("channels_app.meta_status.requests.get", side_effect=_graph(["pages_manage_metadata"])):
			result = check_channel("facebook", CREDS)
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "permission:pages_messaging"), "missing")
		self.assertEqual(_status(result["checks"], "permission:pages_manage_metadata"), "ok")

	def test_missing_subscription_is_reported(self):
		with patch("channels_app.meta_status.requests.get", side_effect=_graph(FB_SCOPES, subscribed=[])):
			result = check_channel("facebook", CREDS)
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "subscription"), "missing")

	def test_invalid_token_stops_after_token_check(self):
		with patch("channels_app.meta_status.requests.get", side_effect=_graph([], valid=False)):
			result = check_channel("facebook", CREDS)
		self.assertFalse(result["ok"])
		self.assertEqual([c["key"] for c in result["checks"]], ["token"])
		self.assertEqual(result["checks"][0]["status"], "error")

	def test_graph_error_is_reported(self):
		err = _resp({"error": {"message": "Unsupported get request."}}, status=400)
		with patch("channels_app.meta_status.requests.get", return_value=err):
			result = check_channel("facebook", CREDS)
		self.assertFalse(result["ok"])
		self.assertEqual(result["checks"][0]["status"], "error")
		self.assertEqual(result["checks"][0]["detail"], "Unsupported get request.")

	def test_instagram_link_mismatch_is_reported(self):
		with patch("channels_app.meta_status.requests.get", side_effect=_graph(IG_SCOPES, ig={"id": "other"})):
			result = check_channel("instagram", CREDS)
		self.assertFalse(result["ok"])
		self.assertEqual(_status(result["checks"], "instagram_link"), "missing")

	def test_missing_credentials_makes_no_graph_call(self):
		empty = SimpleNamespace(page_id="", page_access_token="", meta_app_id="", meta_app_secret="")
		with patch("channels_app.meta_status.requests.get") as get:
			result = check_channel("facebook", empty)
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
