"""
Live Graph API status for a saved Facebook or Instagram channel.

Read-only: it reports which required permissions the Page token has, whether
the Page is subscribed to our messages webhook, and (Instagram) whether the
Instagram account is linked to the Page. It never changes anything on Meta.
"""
import requests

GRAPH_URL = "https://graph.facebook.com/v21.0"
TIMEOUT = 10
SUBSCRIBED_FIELD = "messages"
REQUIRED_PERMISSIONS = {
	"facebook": ["pages_messaging", "pages_manage_metadata"],
	"instagram": ["instagram_basic", "instagram_manage_messages", "pages_manage_metadata"],
}


class GraphError(Exception):
	pass


def _get(path: str, **params) -> dict:
	try:
		resp = requests.get(f"{GRAPH_URL}/{path}", params=params, timeout=TIMEOUT)
	except requests.RequestException:
		raise GraphError("Could not reach Meta. Try again in a moment.")
	try:
		data = resp.json()
	except ValueError:
		data = {}
	if resp.status_code != 200 or "error" in data:
		# Meta's message only; the token is never echoed back
		message = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
		raise GraphError(message)
	return data


def subscribe_page(creds) -> None:
	"""Subscribe the Page to our app's messages webhook. Raises GraphError on failure."""
	page_id = getattr(creds, "page_id", "") or ""
	token = getattr(creds, "page_access_token", "") or ""
	if not (page_id and token):
		raise GraphError("Page ID and Page token are needed to subscribe.")
	try:
		resp = requests.post(
			f"{GRAPH_URL}/{page_id}/subscribed_apps",
			data={"subscribed_fields": SUBSCRIBED_FIELD, "access_token": token},
			timeout=TIMEOUT,
		)
	except requests.RequestException:
		raise GraphError("Could not reach Meta. Try again in a moment.")
	try:
		data = resp.json()
	except ValueError:
		data = {}
	if resp.status_code != 200 or not data.get("success"):
		message = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
		raise GraphError(message)


def _item(key: str, label: str, status: str, detail: str = "") -> dict:
	return {"key": key, "label": label, "status": status, "detail": detail}


def check_channel(channel: str, creds) -> dict:
	"""Return the live status of a channel. `creds` exposes the credential fields as attributes."""
	if channel not in REQUIRED_PERMISSIONS:
		return {"ok": False, "error": "Status checks cover Facebook and Instagram channels only.", "checks": []}

	page_id = getattr(creds, "page_id", "") or ""
	token = getattr(creds, "page_access_token", "") or ""
	app_id = getattr(creds, "meta_app_id", "") or ""
	app_secret = getattr(creds, "meta_app_secret", "") or ""
	if not (page_id and token and app_id and app_secret):
		return {"ok": False, "error": "Page ID, Page token, App ID and App secret are all needed for a status check.", "checks": []}

	checks = []
	try:
		debug = _get("debug_token", input_token=token, access_token=f"{app_id}|{app_secret}")["data"]
		if not debug.get("is_valid"):
			checks.append(_item("token", "Page access token", "error", "The token is invalid or expired. Paste a new one."))
			return {"ok": False, "error": None, "checks": checks}
		checks.append(_item("token", "Page access token", "ok", "Valid"))

		granted = set(debug.get("scopes") or [])
		for perm in REQUIRED_PERMISSIONS[channel]:
			ok = perm in granted
			checks.append(_item(
				f"permission:{perm}", perm, "ok" if ok else "missing",
				"Granted" if ok else "Required but not granted on this token",
			))

		subs = _get(f"{page_id}/subscribed_apps", access_token=token).get("data", [])
		app_sub = next((s for s in subs if str(s.get("id")) == str(app_id)), None)
		subscribed = bool(app_sub) and SUBSCRIBED_FIELD in (app_sub.get("subscribed_fields") or [])
		checks.append(_item(
			"subscription", f"Webhook subscription ({SUBSCRIBED_FIELD})",
			"ok" if subscribed else "missing",
			"Page sends messages to us" if subscribed else "Page is not subscribed to our app's messages",
		))

		if channel == "instagram":
			ig_id = getattr(creds, "instagram_account_id", "") or ""
			linked = _get(page_id, fields="instagram_business_account", access_token=token).get("instagram_business_account") or {}
			linked_ok = bool(ig_id) and str(linked.get("id")) == str(ig_id)
			checks.append(_item(
				"instagram_link", "Instagram account linked to Page", "ok" if linked_ok else "missing",
				"Linked" if linked_ok else "The Instagram account is not linked to this Page",
			))
	except GraphError as e:
		checks.append(_item("graph", "Meta check", "error", str(e)))
		return {"ok": False, "error": None, "checks": checks}

	return {"ok": all(c["status"] == "ok" for c in checks), "error": None, "checks": checks}
