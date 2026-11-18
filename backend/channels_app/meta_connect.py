import base64
import hashlib
import hmac
import json
import secrets

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q

from channels_app.credential_crypto import decrypt_secret, encrypt_secret
from channels_app.meta_status import GraphError
from channels_app.models import ChannelCredential, FacebookCredentials, InstagramCredentials, Platform

GRAPH_URL = "https://graph.facebook.com/v21.0"
TIMEOUT = 10
SUBSCRIBED_FIELDS = "messages,messaging_postbacks"
MANAGE_PERMS = {"ADMIN", "MODERATE"}
SETUP_TTL_SECONDS = 600
SETUP_CACHE_PREFIX = "meta_setup:"
SETUP_CLAIM_PREFIX = "meta_setup_claim:"


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


def claim_setup_session(setup_id: str) -> bool:
	# cache.add is atomic: exactly one of several concurrent connects for one setup wins the claim
	return cache.add(f"{SETUP_CLAIM_PREFIX}{setup_id}", 1, timeout=SETUP_TTL_SECONDS)


def release_setup_session(setup_id: str) -> None:
	# A failed connect frees the claim so the customer can retry with the same setup_id
	cache.delete(f"{SETUP_CLAIM_PREFIX}{setup_id}")


def consume_setup_session(setup_id: str) -> dict | None:
	# cache.delete returns True for exactly one caller, so a parallel second consume gets None
	payload = read_setup_session(setup_id)
	if payload is None:
		return None
	return payload if cache.delete(_session_key(setup_id)) else None


def connect_page(project, page: dict, knowledge_base, meta_user_id: str) -> list[ChannelCredential]:
	page_id = page["id"]
	ig_id = page.get("instagram_account_id") or ""
	owner = Q(credentials__page_id=page_id)
	if ig_id:
		owner |= Q(credentials__instagram_account_id=ig_id)
	if ChannelCredential.objects.filter(owner, is_active=True).exclude(project=project).exists():
		raise PageConflict(page_id)
	# Subscribe before any local write so a Graph failure leaves existing rows untouched
	subscribe_page_for_oauth(page_id, page["access_token"])
	common = {"page_id": page_id, "page_access_token": page["access_token"],
		"meta_app_id": settings.META_APP_ID, "connected_via": "oauth", "meta_user_id": meta_user_id}
	targets = [("facebook", f"Messenger — {page['name']}", FacebookCredentials(**common))]
	if ig_id:
		targets.append(("instagram", f"Instagram — @{page.get('instagram_username', '')}",
			InstagramCredentials(instagram_account_id=ig_id, **common)))
	rows = []
	with transaction.atomic():
		for channel, name, creds in targets:
			row = ChannelCredential.objects.filter(project=project, channel=channel,
				credentials__page_id=page_id).first()
			if row is None:
				row = ChannelCredential(project=project, channel=channel, name=name,
					platform=Platform.objects.filter(name=channel).first())
			row.set_credentials(creds)  # encrypts the token and drops stale app secret / verify token keys
			row.knowledge_base = knowledge_base
			row.is_active = True
			row.save()
			rows.append(row)
	return rows
