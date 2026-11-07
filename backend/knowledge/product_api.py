"""REST API product sources: validate the saved request, keep its secret encrypted, and read the rows it returns."""
import base64
import hashlib
import json
import re

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from agents.api_tool import _is_public_host, _parse_target
from .product_sources import MAX_FILE_BYTES, TableError

METHODS = ("GET", "POST")
AUTH_TYPES = ("none", "bearer", "header", "query")
MAX_API_PAGES = 20
MAX_API_ROWS = 5000
API_TIMEOUT = 15
MAX_PAIRS = 30
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
# Names that look like credentials are refused as plain headers/params: a key there would be stored and sent back to the browser
_CREDENTIAL_WORDS = ("key", "token", "auth", "secret", "cookie", "session", "password")


def _fernet() -> Fernet:
	# Derived from SECRET_KEY: no new env var. Rotating SECRET_KEY makes saved keys unreadable (sync says so).
	key = base64.urlsafe_b64encode(hashlib.sha256(("product-source:" + settings.SECRET_KEY).encode()).digest())
	return Fernet(key)


def encrypt_blob(data: dict) -> str:
	"""The key and the request body, encrypted together: neither is stored in clear text."""
	return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_blob(token: str) -> dict:
	if not token:
		return {}
	try:
		return json.loads(_fernet().decrypt(token.encode()))
	except InvalidToken:
		raise TableError("secret_unreadable", "The saved API key can no longer be read. Add this source again with its key.")


def _pairs(value, label: str, check_credentials: bool) -> dict[str, str]:
	if value in (None, ""):
		return {}
	if not isinstance(value, dict) or len(value) > MAX_PAIRS:
		raise TableError("invalid_value", f"The {label} must be a list of up to {MAX_PAIRS} name and value pairs")
	out = {}
	for name, val in value.items():
		name = str(name).strip()
		if not _NAME.match(name) or name.lower() == "host":
			raise TableError("invalid_value", f"{name!r} is not a valid {label} name")
		if check_credentials and any(w in name.lower() for w in _CREDENTIAL_WORDS):
			raise TableError("invalid_value", f"Put {name} in the Authentication fields, not in {label}s")
		if not isinstance(val, (str, int, float)) or isinstance(val, bool) or "\r" in str(val) or "\n" in str(val) or len(str(val)) > 500:
			raise TableError("invalid_value", f"The value of {name} is not valid")
		out[name] = str(val)
	return out


def clean_api_config(raw) -> dict:
	"""The saved request, minus any secret. Raises TableError with a message the user can act on."""
	if not isinstance(raw, dict):
		raise TableError("invalid_value", "The API settings must be an object")
	method = str(raw.get("method") or "GET").upper()
	if method not in METHODS:
		raise TableError("invalid_value", "The method must be GET or POST")
	url = str(raw.get("url") or "").strip()
	try:
		scheme, host = _parse_target(url)
	except ValueError as e:
		raise TableError("invalid_url", f"Enter a valid API URL: {e}")
	if scheme != "https" or not host:
		raise TableError("invalid_url", "The API URL must start with https://")
	auth = str(raw.get("auth_type") or "none")
	if auth not in AUTH_TYPES:
		raise TableError("invalid_value", "Choose an authentication type")
	auth_name = str(raw.get("auth_name") or "").strip()
	if auth in ("header", "query") and not _NAME.match(auth_name):
		raise TableError("invalid_value", "Name the header or query parameter that carries the key")
	body = raw.get("body")
	if body not in (None, "") and (method != "POST" or not isinstance(body, dict)):
		raise TableError("invalid_value", "A request body needs the POST method and must be a JSON object")
	response_path = str(raw.get("response_path") or "").strip()
	if response_path and not _PATH.match(response_path):
		raise TableError("invalid_value", "The response path can use letters, numbers, dots, dashes and underscores")
	page_param = str(raw.get("page_param") or "").strip()
	if page_param and not _NAME.match(page_param):
		raise TableError("invalid_value", "The page parameter name is not valid")
	try:
		max_pages = int(raw.get("max_pages") or 1)
	except (TypeError, ValueError):
		raise TableError("invalid_value", "Max pages must be a whole number")
	return {
		"method": method, "url": url,
		"headers": _pairs(raw.get("headers"), "header", check_credentials=True),
		"query": _pairs(raw.get("query"), "query parameter", check_credentials=True),
		"auth_type": auth, "auth_name": auth_name if auth in ("header", "query") else "",
		"body": body or None, "response_path": response_path,
		"page_param": page_param, "max_pages": min(max(max_pages, 1), MAX_API_PAGES) if page_param else 1,
	}


def clean_secret(auth_type: str, secret: str) -> str:
	secret = (secret or "").strip()
	if auth_type != "none" and not secret:
		raise TableError("invalid_value", "Enter the API key or token")
	if len(secret) > 2000:
		raise TableError("invalid_value", "The API key is too long")
	return secret if auth_type != "none" else ""


def _flatten(value) -> str:
	if value is None:
		return ""
	if isinstance(value, bool):
		return "true" if value else "false"
	if isinstance(value, float) and value.is_integer():
		return str(int(value))
	if isinstance(value, list) and all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in value):
		return ", ".join(_flatten(v) for v in value)  # a list of links or values reads like a sheet cell
	if isinstance(value, (dict, list)):
		return json.dumps(value, ensure_ascii=False)
	return str(value).strip()


def _walk(data, path: str):
	for key in filter(None, path.split(".")):
		if not isinstance(data, dict) or key not in data:
			raise TableError("invalid_response", f"The response has no field named {key}")
		data = data[key]
	return data


def _read_capped(resp) -> bytes:
	buf = bytearray()
	for chunk in resp.iter_content(chunk_size=64 * 1024):
		buf.extend(chunk)
		if len(buf) > MAX_FILE_BYTES:
			raise TableError("file_too_large", "The API returned more than 5 MB")
	return bytes(buf)


def _get_page(config: dict, headers: dict, query: dict) -> list[dict]:
	kwargs = {"json": config["body"]} if config["method"] == "POST" and config["body"] else {}
	try:
		# No redirects: a 3xx could point at an internal host. Errors never echo the URL, which can carry a key.
		resp = requests.request(config["method"], config["url"], headers=headers, params=query,
			timeout=API_TIMEOUT, allow_redirects=False, stream=True, **kwargs)
	except requests.RequestException:
		raise TableError("api_unreachable", "Could not reach the API. Check the URL and try again.")
	try:
		if 300 <= resp.status_code < 400:
			raise TableError("redirect_not_followed", "The API redirected. Use its final URL instead.")
		if resp.status_code != 200:
			raise TableError("api_error", f"The API answered with status {resp.status_code}")
		body = _read_capped(resp)
	except requests.RequestException:
		raise TableError("api_unreachable", "The API connection broke while reading. Try again.")
	finally:
		resp.close()
	try:
		data = json.loads(body)
	except ValueError:
		raise TableError("invalid_response", "The API response is not JSON")
	items = _walk(data, config["response_path"])
	if not isinstance(items, list):
		raise TableError("invalid_response", "The data at that place in the response is not a list")
	return [i for i in items if isinstance(i, dict)]


def fetch_api_table(config: dict, secret: str) -> tuple[list[str], list[dict]]:
	"""(headers, rows) like parse_table: every row is a dict of strings. Pages until an empty page or max_pages."""
	_, host = _parse_target(config["url"])
	if not _is_public_host(host):
		raise TableError("blocked_host", "The API host must be a public address")
	headers, query = dict(config["headers"]), dict(config["query"])
	if config["auth_type"] == "bearer":
		headers["Authorization"] = f"Bearer {secret}"
	elif config["auth_type"] == "header":
		headers[config["auth_name"]] = secret
	elif config["auth_type"] == "query":
		query[config["auth_name"]] = secret
	records: list[dict] = []  # keys stripped so a name with stray spaces still lines up with its column
	previous_first = None
	more = False  # True when the last page we read was full: the API has rows past Max pages
	for page in range(1, config["max_pages"] + 1):
		params = {**query, config["page_param"]: page} if config["page_param"] else query
		items = _get_page(config, headers, params)
		if not items:
			more = False
			break
		first = json.dumps(items[0], sort_keys=True)
		if first == previous_first:  # the API ignores the page number: stop instead of repeating rows
			more = False
			break
		previous_first = first
		records.extend({str(k).strip(): v for k, v in i.items()} for i in items)
		more = True
		if len(records) > MAX_API_ROWS:
			raise TableError("too_many_rows", f"The API returned more than {MAX_API_ROWS} rows")
	# Stopping early would hide the products on the missing pages at every sync, so refuse instead
	if more and config["page_param"]:
		raise TableError("too_many_pages", f"The API has more pages than Max pages ({config['max_pages']}). Raise Max pages, up to {MAX_API_PAGES}.")
	names: dict[str, None] = {}
	for rec in records:
		for key in rec:
			if str(key).strip():
				names.setdefault(str(key).strip(), None)
	if records and not names:
		raise TableError("invalid_response", "The API rows have no named fields")
	cols = list(names)
	return cols, [{c: _flatten(rec.get(c)) for c in cols} for rec in records]
