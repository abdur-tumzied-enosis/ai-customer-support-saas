import ipaddress
import re

from agents.api_tool import _parse_target
from .models import Order
from .templates import LIVE_PLACEHOLDERS, SAMPLE_CONTEXT, TemplateError, render, validate_json_template

HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
RESERVED_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}
MAX_HEADERS = 5
MAX_HEADER_VALUE = 500
MAPPABLE = {"tracking_number"}
MAPPING_PATH = re.compile(r"^[A-Za-z_]\w*(\.\w+)*$")  # e.g. data.tracking or data.items.0.code
STATUSES = {code for code, _ in Order.STATUS_CHOICES} - {"draft"}


class ActionError(Exception):
	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code, self.message = code, message


def header_summary(headers: list[dict]) -> list[dict]:
	"""Header names and whether a value is stored. Values are never returned."""
	return [{"name": h["name"], "has_value": bool(h.get("value"))} for h in headers]


def clean_headers(incoming: list[dict], existing: list[dict]) -> list[dict]:
	"""A blank value on an existing header name keeps the stored secret."""
	if len(incoming) > MAX_HEADERS:
		raise ActionError("invalid_headers", f"At most {MAX_HEADERS} headers")
	stored = {h["name"].lower(): h["value"] for h in existing}
	cleaned = []
	for h in incoming:
		name, value = str(h.get("name") or "").strip(), str(h.get("value") or "")
		if not HEADER_NAME.match(name):
			raise ActionError("invalid_headers", f"Header name '{name}' is not valid")
		if name.lower() in RESERVED_HEADERS:
			raise ActionError("invalid_headers", f"Header '{name}' cannot be set")
		if len(value) > MAX_HEADER_VALUE:
			raise ActionError("invalid_headers", f"Header '{name}' value is too long")
		if not value:
			value = stored.get(name.lower(), "")
			if not value:
				raise ActionError("invalid_headers", f"Header '{name}' needs a value")
		cleaned.append({"name": name, "value": value})
	return cleaned


def validate_action(data: dict, existing: list[dict]) -> dict:
	"""Cleaned model fields for one action. Raises ActionError with a code the API returns."""
	name = str(data.get("name") or "").strip()
	if not name or len(name) > 255:
		raise ActionError("invalid_name", "Name is required (at most 255 characters)")
	method = data.get("method")
	if method not in ("GET", "POST"):
		raise ActionError("invalid_method", "Method must be GET or POST")
	url = str(data.get("url") or "").strip()
	try:
		_check_url(render(url, SAMPLE_CONTEXT, "url"))  # placeholders must resolve: unknown names and order.items fail here
	except TemplateError as e:
		raise ActionError(e.code, e.message)
	body = data.get("body_template") or ""
	if method == "GET" and body:
		raise ActionError("invalid_body_template", "A GET action takes no body")
	if body:
		try:
			validate_json_template(body)
		except TemplateError as e:
			raise ActionError(e.code, e.message)
	mapping = data.get("response_mapping") or {}
	for key, path in mapping.items():
		if key not in MAPPABLE or not isinstance(path, str) or not MAPPING_PATH.match(path):
			raise ActionError("invalid_mapping", "Response mapping supports tracking_number with a dotted path")
	status = data.get("success_status") or ""
	if status and status not in STATUSES:
		raise ActionError("invalid_status", "Success status must be an order status")
	codes = data.get("success_codes") or [200, 201, 202, 204]
	if not isinstance(codes, list) or not all(isinstance(c, int) and 200 <= c <= 299 for c in codes):
		raise ActionError("invalid_codes", "Success codes must be whole numbers from 200 to 299")
	is_active = bool(data.get("is_active", True))
	auto_run = bool(data.get("auto_run", False))
	if auto_run and not is_active:
		raise ActionError("auto_run_needs_active", "Only an active action can run automatically")
	return {
		"name": name, "method": method, "url": url, "headers": clean_headers(data.get("headers") or [], existing),
		"body_template": body, "response_mapping": mapping, "success_status": status, "success_codes": codes,
		"auto_run": auto_run, "is_active": is_active,
	}


def _check_url(url: str) -> None:
	"""http(s) only, and an IP literal must be public. Names are checked again when a request is made."""
	try:
		scheme, host = _parse_target(url)
	except ValueError:
		raise ActionError("invalid_url", "URL is not valid")
	if scheme not in ("http", "https") or not host:
		raise ActionError("invalid_url", "URL must start with http:// or https://")
	try:
		if not ipaddress.ip_address(host.strip("[]")).is_global:
			raise ActionError("internal_url", "URL points to a non-public address")
	except ValueError:
		pass  # a host name, not an IP literal


def clean_live_lookup(value, existing: dict) -> dict:
	"""Live price settings. A blank header value keeps the stored secret, as for actions."""
	if not isinstance(value, dict):
		raise ActionError("invalid_live_lookup", "Live lookup must be an object")
	enabled = bool(value.get("enabled"))
	url = str(value.get("url_template") or "").strip()
	price_path = str(value.get("price_path") or "").strip()
	available_path = str(value.get("available_path") or "").strip() or None
	if enabled and not (url and price_path):
		raise ActionError("invalid_live_lookup", "Live lookup needs a URL template and a price path")
	if url:
		try:
			sample = render(url, {"product.external_id": "x", "product.sku": "x"}, "url", LIVE_PLACEHOLDERS)
		except TemplateError as e:
			raise ActionError("invalid_live_lookup", e.message)
		_check_url(sample)
	for path in (price_path, available_path):
		if path and not MAPPING_PATH.match(path):
			raise ActionError("invalid_live_lookup", f"'{path}' is not a dotted path")
	return {
		"enabled": enabled, "url_template": url, "price_path": price_path, "available_path": available_path,
		"headers": clean_headers(value.get("headers") or [], existing.get("headers") or []),
	}
