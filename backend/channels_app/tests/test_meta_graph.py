import base64
import hashlib
import hmac
import json
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from channels_app.meta_connect import (
	GRAPH_URL, create_setup_session, consume_setup_session, exchange_long_lived_token,
	get_meta_user_id, list_manageable_pages, read_setup_session, subscribe_page_for_oauth, unsubscribe_page,
	verify_signed_request,
)
from channels_app.meta_status import GraphError

TEST_KEY = "DdZ_qWzIlhW-IcbMX1pHYAAqQNYv9h9rcyfDRCeRwPY="
# Local memory, so tearDown's cache.clear() never flushes the live Redis the dev stack uses
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _resp(body, status=200):
	r = mock.Mock(status_code=status)
	r.json.return_value = body
	return r


def _b64(raw: bytes) -> bytes:
	return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _signed(data, secret="sec"):
	payload = _b64(json.dumps(data).encode())
	sig = _b64(hmac.new(secret.encode(), payload, hashlib.sha256).digest())
	return (sig + b"." + payload).decode()


@override_settings(TOKEN_ENCRYPTION_KEY=TEST_KEY, META_APP_ID="1478910681051836", META_APP_SECRET="sec",
	CACHES=LOCMEM_CACHE)
class MetaGraphTest(SimpleTestCase):
	def tearDown(self):
		cache.clear()

	def test_token_exchange_uses_app_credentials_and_returns_long_lived_token(self):
		with mock.patch("channels_app.meta_connect.requests.get", return_value=_resp({"access_token": "long"})) as get:
			assert exchange_long_lived_token("short") == "long"
		assert get.call_args.args[0] == f"{GRAPH_URL}/oauth/access_token"
		assert get.call_args.kwargs["params"]["fb_exchange_token"] == "short"
		assert get.call_args.kwargs["params"]["client_id"] == "1478910681051836"

	def test_meta_user_id_is_read_from_me(self):
		with mock.patch("channels_app.meta_connect.requests.get", return_value=_resp({"id": "u1"})) as get:
			assert get_meta_user_id("long") == "u1"
		assert get.call_args.args[0] == f"{GRAPH_URL}/me"

	def test_graph_error_is_surfaced(self):
		with mock.patch("channels_app.meta_connect.requests.get",
				return_value=_resp({"error": {"message": "Invalid token"}}, status=400)):
			with self.assertRaises(GraphError) as ctx:
				exchange_long_lived_token("bad")
		assert str(ctx.exception) == "Invalid token"

	def test_page_listing_keeps_manage_tasks_only_and_extracts_instagram(self):
		payload = {"data": [
			{"id": "1", "name": "Admin Page", "access_token": "p1", "tasks": ["MANAGE", "MESSAGING"],
				"instagram_business_account": {"id": "ig1", "username": "shop"}},
			{"id": "2", "name": "Viewer Page", "access_token": "p2", "tasks": ["MESSAGING"],
				"instagram_business_account": None},
			{"id": "3", "name": "Tasks Page", "access_token": "p3", "tasks": ["MANAGE"]},
		]}
		with mock.patch("channels_app.meta_connect.requests.get", return_value=_resp(payload)) as get:
			pages = list_manageable_pages("long")
		assert "perms" not in get.call_args.kwargs["params"]["fields"]
		assert [p["id"] for p in pages] == ["1", "3"]
		assert pages[0]["instagram_account_id"] == "ig1"
		assert pages[0]["instagram_username"] == "shop"
		assert pages[1]["instagram_account_id"] == ""

	def test_page_listing_follows_pagination(self):
		first = {"data": [{"id": "1", "name": "A", "access_token": "a", "tasks": ["MANAGE"]}],
			"paging": {"next": "https://graph.facebook.com/next"}}
		second = {"data": [{"id": "2", "name": "B", "access_token": "b", "tasks": ["MANAGE"]}]}
		with mock.patch("channels_app.meta_connect.requests.get", side_effect=[_resp(first), _resp(second)]):
			assert [p["id"] for p in list_manageable_pages("long")] == ["1", "2"]

	def test_subscribe_posts_both_fields_and_failure_raises(self):
		with mock.patch("channels_app.meta_connect.requests.post", return_value=_resp({"success": True})) as post:
			subscribe_page_for_oauth("1", "p1")
		assert post.call_args.args[0] == f"{GRAPH_URL}/1/subscribed_apps"
		assert post.call_args.kwargs["data"]["subscribed_fields"] == "messages,messaging_postbacks"
		with mock.patch("channels_app.meta_connect.requests.post",
				return_value=_resp({"error": {"message": "No permission"}}, status=400)):
			with self.assertRaises(GraphError):
				subscribe_page_for_oauth("1", "p1")

	def test_unsubscribe_sends_delete(self):
		with mock.patch("channels_app.meta_connect.requests.delete", return_value=_resp({"success": True})) as delete:
			unsubscribe_page("1", "p1")
		assert delete.call_args.args[0] == f"{GRAPH_URL}/1/subscribed_apps"

	def test_signed_request_verification(self):
		good = _signed({"user_id": "u1"})
		assert verify_signed_request(good, "sec") == {"user_id": "u1"}
		assert verify_signed_request(good, "wrong") is None
		assert verify_signed_request("garbage", "sec") is None
		assert verify_signed_request("sig.é", "sec") is None  # non-ASCII must not raise
		bad_alg = _signed({"user_id": "u1", "algorithm": "HMAC-SHA1"})
		assert verify_signed_request(bad_alg, "sec") is None

	def test_session_is_encrypted_in_cache_and_consumed_once(self):
		setup_id = create_setup_session("u1", "p1", "mu1", "LONG-TOKEN-xyz",
			[{"id": "1", "access_token": "PAGE-TOKEN-xyz"}])
		raw = cache.get(f"meta_setup:{setup_id}")
		assert raw is not None
		assert "PAGE-TOKEN-xyz" not in raw and "LONG-TOKEN-xyz" not in raw
		assert read_setup_session(setup_id)["pages"][0]["id"] == "1"
		assert consume_setup_session(setup_id)["meta_user_id"] == "mu1"
		assert read_setup_session(setup_id) is None
		assert consume_setup_session(setup_id) is None
