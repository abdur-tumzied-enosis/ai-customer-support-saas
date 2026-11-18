import base64
import hashlib
import hmac
import json
import secrets

import requests
from django.conf import settings
from django.core.cache import cache

from channels_app.credential_crypto import decrypt_secret, encrypt_secret
from channels_app.meta_status import GraphError

GRAPH_URL = "https://graph.facebook.com/v21.0"
TIMEOUT = 10
SUBSCRIBED_FIELDS = "messages,messaging_postbacks"
MANAGE_PERMS = {"ADMIN", "MODERATE"}
SETUP_TTL_SECONDS = 600
SETUP_CACHE_PREFIX = "meta_setup:"


class PageConflict(Exception):
	pass


def _json(resp) -> dict:
	try:
		body = resp.json()
	except ValueError:
		body = {}
	if resp.status_code != 200 or "error" in body:
		raise GraphError((body.get("error") or {}).get("message") or "Meta request failed")
	return body


def _call(method: str, url: str, **kwargs) -> dict:
	try:
		resp = getattr(requests, method)(url, timeout=TIMEOUT, **kwargs)
	except requests.RequestException:
		# The exception text embeds the request URL, which carries tokens and the app secret; never surface it
		raise GraphError("Could not reach Meta") from None
	return _json(resp)


def exchange_long_lived_token(short_lived_token: str) -> str:
	body = _call("get", f"{GRAPH_URL}/oauth/access_token", params={
		"grant_type": "fb_exchange_token",
		"client_id": settings.META_APP_ID,
		"client_secret": settings.META_APP_SECRET,
		"fb_exchange_token": short_lived_token,
	})
	return body["access_token"]


def get_meta_user_id(user_token: str) -> str:
	body = _call("get", f"{GRAPH_URL}/me", params={"fields": "id", "access_token": user_token})
	return str(body["id"])


def _can_manage(item: dict) -> bool:
	perms = item.get("perms")
	if isinstance(perms, str):
		perms = perms.split(",")
	if perms:
		return bool(MANAGE_PERMS & {str(p).strip() for p in perms})
	return "MANAGE" in (item.get("tasks") or [])


def list_manageable_pages(user_token: str) -> list[dict]:
	pages = []
	url = f"{GRAPH_URL}/me/accounts"
	params = {
		"fields": "id,name,access_token,perms,tasks,instagram_business_account{id,username}",
		"limit": 100,
		"access_token": user_token,
	}
	while url:
		body = _call("get", url, params=params)
		for item in body.get("data", []):
			if not _can_manage(item):
				continue
			ig = item.get("instagram_business_account") or {}
			pages.append({
				"id": str(item["id"]),
				"name": item.get("name", ""),
				"access_token": item["access_token"],
				"instagram_account_id": str(ig.get("id", "")),
				"instagram_username": ig.get("username", ""),
			})
		url = (body.get("paging") or {}).get("next")
		params = None  # the next link already carries its query string
	return pages


def subscribe_page_for_oauth(page_id: str, page_token: str) -> None:
	_call("post", f"{GRAPH_URL}/{page_id}/subscribed_apps",
		data={"subscribed_fields": SUBSCRIBED_FIELDS, "access_token": page_token})


def unsubscribe_page(page_id: str, page_token: str) -> None:
	_call("delete", f"{GRAPH_URL}/{page_id}/subscribed_apps", data={"access_token": page_token})


def _pad(part: str) -> str:
	return part + "=" * (-len(part) % 4)


def verify_signed_request(signed_request: str, app_secret: str) -> dict | None:
	try:
		encoded_sig, encoded_payload = signed_request.split(".", 1)
		sig = base64.urlsafe_b64decode(_pad(encoded_sig))
		expected = hmac.new(app_secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
		if not hmac.compare_digest(sig, expected):
			return None
		data = json.loads(base64.urlsafe_b64decode(_pad(encoded_payload)))
	except (ValueError, TypeError):
		return None
	if not isinstance(data, dict) or data.get("algorithm", "HMAC-SHA256") != "HMAC-SHA256":
		return None
	return data


def _session_key(setup_id: str) -> str:
	return f"{SETUP_CACHE_PREFIX}{setup_id}"


def create_setup_session(user_id: str, project_id: str, meta_user_id: str,
		long_lived_token: str, pages: list[dict]) -> str:
	setup_id = secrets.token_urlsafe(32)
	payload = {"user_id": user_id, "project_id": project_id, "meta_user_id": meta_user_id,
		"long_lived_token": long_lived_token, "pages": pages}
	cache.set(_session_key(setup_id), encrypt_secret(json.dumps(payload)), timeout=SETUP_TTL_SECONDS)
	return setup_id


def read_setup_session(setup_id: str) -> dict | None:
	cached = cache.get(_session_key(setup_id))
	if cached is None:
		return None
	return json.loads(decrypt_secret(cached))


def consume_setup_session(setup_id: str) -> dict | None:
	# cache.delete returns True for exactly one caller, so a parallel second consume gets None
	payload = read_setup_session(setup_id)
	if payload is None:
		return None
	return payload if cache.delete(_session_key(setup_id)) else None
