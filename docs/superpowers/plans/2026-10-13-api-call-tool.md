# API Call Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the reply agent make GET/POST calls to business APIs, enabled per custom instruction with an allowed-domain list and server-side secrets.

**Architecture:** A pure `agents/api_tool.py` builds a `call_api` closure that enforces method, domain allowlist, SSRF guard, limits and injects secrets. `knowledge/api_tool_config.py` validates/normalizes the instruction's `api_domains` and hides secrets in API output. `channels_app/tasks.py` unions domains from effective instructions into `rag.Context`; the call log lands in the outbound message's `meta["api_calls"]`. The knowledge page gets an `ApiAccessEditor` component in the instruction dialogs.

**Tech Stack:** Django 5 + DRF, LangChain `create_agent`, `requests`, stdlib `ipaddress`/`socket`/`urllib.parse`; Next.js + React + Tailwind (existing `components/ui`).

**Spec:** `docs/superpowers/specs/2026-10-13-api-call-tool-design.md`

## Global Constraints

- Match each file's existing indentation: tabs in `agents/`, `channels_app/`, `knowledge/models.py`, new modules and tests; **4 spaces** in `knowledge/views.py`.
- Methods: `GET`, `POST` only. Schemes: `http`, `https`.
- Limits: 5 calls per reply, 10 s timeout, 20 000-byte response cap, max 10 domains per instruction.
- Secrets never returned by the API, never logged, never visible to the LLM.
- Tool absent unless an effective instruction has `api_tool_enabled=True` and non-empty `api_domains`.
- No new Python or npm dependencies.
- Spec deviation (deliberate): spec §3 says "serializer"; the instruction views hand-build response dicts and the `CustomInstructionSerializer` class is unused, so validation lives in `knowledge/api_tool_config.py` and output in one `_instruction_to_dict` helper in `knowledge/views.py`.

## How to run things

- Backend tests (Postgres + Qdrant come up via compose):
  `docker compose run --rm django python manage.py test <dotted.module> -v 2` from repo root.
- Migrations: `docker compose run --rm django python manage.py makemigrations knowledge`
- Frontend checks: `cd frontend && npx tsc --noEmit && npm run lint`

## Review Focus

1. LLM sends `method="get"` (lowercase) → accepted as GET. Test in Task 1.
2. LLM sends a `body` with a GET → body silently not sent (no error). Test in Task 1.
3. URL host with trailing dot or uppercase (`https://API.Shop.com./x`) → matches `shop.com`. Test in Task 1.
4. Hostname that does not resolve (DNS failure) → `ERROR:` string, no exception escapes the tool. Test in Task 1.
5. API returns non-UTF-8/binary bytes → decoded with replacement chars, no crash. Test in Task 1.

---

### Task 1: `call_api` tool module

**Files:**
- Create: `backend/agents/api_tool.py`
- Create: `backend/agents/tests/__init__.py` (empty; enables test discovery)
- Test: `backend/agents/tests/test_api_tool.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `build_call_api_tool(domains: list[dict], log: list) -> Callable[[str, str, Optional[dict]], str]` — returned function is named `call_api`.
  - `collect_api_domains(instructions: list[dict]) -> list[dict]` — input dicts have `api_tool_enabled: bool`, `api_domains: list[dict]`; newest instruction first; first occurrence of a domain wins.
  - Domain dict shape: `{"domain": str, "auth_header"?: str, "secret"?: str}`.
  - Log entry shape: `{"method": str, "url": str, "status"?: int, "error"?: str, "ms": int}`.

- [ ] **Step 1: Create empty `backend/agents/tests/__init__.py`.**

- [ ] **Step 2: Write the failing tests** — `backend/agents/tests/test_api_tool.py`:

```python
import socket
from unittest import mock

import requests
from django.test import SimpleTestCase

from agents.api_tool import MAX_BYTES, MAX_CALLS, build_call_api_tool, collect_api_domains

DOMAINS = [
	{"domain": "shop.com", "auth_header": "Authorization", "secret": "Bearer s3cret"},
	{"domain": "hooks.zapier.com"},
]


def _addr(ip):
	family = socket.AF_INET6 if ":" in ip else socket.AF_INET
	return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _resp(status=200, body=b'{"ok": true}', encoding="utf-8"):
	resp = mock.MagicMock(status_code=status, encoding=encoding)
	resp.raw.read.return_value = body
	return resp


@mock.patch("agents.api_tool.socket.getaddrinfo", return_value=_addr("93.184.216.34"))
@mock.patch("agents.api_tool.requests.request")
class CallApiTest(SimpleTestCase):
	def _tool(self):
		self.log = []
		return build_call_api_tool(DOMAINS, self.log)

	def test_exact_domain_get_with_secret(self, req, _dns):
		req.return_value = _resp()
		out = self._tool()("GET", "https://shop.com/orders/1")
		assert out == 'HTTP 200\n{"ok": true}'
		_, kwargs = req.call_args
		assert kwargs["headers"] == {"Authorization": "Bearer s3cret"}
		assert kwargs["allow_redirects"] is False
		assert kwargs["timeout"] == 10
		assert self.log[0]["status"] == 200 and self.log[0]["method"] == "GET"
		assert "s3cret" not in str(self.log)

	def test_subdomain_allowed_http_allowed(self, req, _dns):
		req.return_value = _resp()
		assert self._tool()("GET", "http://api.shop.com/x").startswith("HTTP 200")

	def test_lookalike_domains_blocked(self, req, _dns):
		tool = self._tool()
		for url in ("https://evilshop.com/x", "https://shop.com.evil.io/x", "ftp://shop.com/x", "shop.com/x"):
			assert tool("GET", url).startswith("ERROR"), url
		req.assert_not_called()

	def test_secret_only_for_its_domain(self, req, _dns):
		req.return_value = _resp()
		self._tool()("POST", "https://hooks.zapier.com/h", {"a": 1})
		_, kwargs = req.call_args
		assert kwargs["headers"] == {}
		assert kwargs["json"] == {"a": 1}

	def test_private_addresses_blocked(self, req, dns):
		for ip in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "224.0.0.1"):
			dns.return_value = _addr(ip)
			assert self._tool()("GET", "https://shop.com/x").startswith("ERROR"), ip
		req.assert_not_called()

	def test_put_rejected_lowercase_get_accepted(self, req, _dns):
		req.return_value = _resp()
		tool = self._tool()
		assert tool("PUT", "https://shop.com/x").startswith("ERROR")
		assert tool("get", "https://shop.com/x").startswith("HTTP 200")

	def test_get_body_not_sent(self, req, _dns):
		req.return_value = _resp()
		self._tool()("GET", "https://shop.com/x", {"ignored": True})
		_, kwargs = req.call_args
		assert kwargs["json"] is None

	def test_trailing_dot_and_uppercase_host(self, req, _dns):
		req.return_value = _resp()
		assert self._tool()("GET", "https://API.Shop.com./x").startswith("HTTP 200")

	def test_dns_failure_returns_error(self, req, dns):
		dns.side_effect = socket.gaierror("nope")
		assert self._tool()("GET", "https://shop.com/x").startswith("ERROR")
		req.assert_not_called()

	def test_large_response_truncated(self, req, _dns):
		req.return_value = _resp(body=b"x" * (MAX_BYTES + 500))
		out = self._tool()("GET", "https://shop.com/x")
		assert out.endswith("[truncated]")
		assert out.count("x") == MAX_BYTES

	def test_binary_response_does_not_crash(self, req, _dns):
		req.return_value = _resp(body=b"\xff\xfe\x00bad", encoding=None)
		assert self._tool()("GET", "https://shop.com/x").startswith("HTTP 200")

	def test_call_cap(self, req, _dns):
		req.return_value = _resp()
		tool = self._tool()
		for _ in range(MAX_CALLS):
			assert tool("GET", "https://shop.com/x").startswith("HTTP")
		assert "limit" in tool("GET", "https://shop.com/x")

	def test_timeout_and_connection_error(self, req, _dns):
		req.side_effect = requests.Timeout()
		assert self._tool()("GET", "https://shop.com/x") == "ERROR: timeout"
		assert self.log[0]["error"] == "timeout"
		req.side_effect = requests.ConnectionError()
		assert self._tool()("GET", "https://shop.com/x").startswith("ERROR")

	def test_redirect_returned_not_followed(self, req, _dns):
		req.return_value = _resp(status=302, body=b"")
		assert self._tool()("GET", "https://shop.com/x").startswith("HTTP 302")


class CollectApiDomainsTest(SimpleTestCase):
	def test_union_skips_disabled_and_newest_wins(self):
		instructions = [
			{"api_tool_enabled": True, "api_domains": [{"domain": "shop.com", "secret": "new"}]},
			{"api_tool_enabled": False, "api_domains": [{"domain": "off.com"}]},
			{"api_tool_enabled": True, "api_domains": [{"domain": "shop.com", "secret": "old"}, {"domain": "b.com"}]},
			{"label": "legacy", "message": "no api keys at all"},
		]
		assert collect_api_domains(instructions) == [{"domain": "shop.com", "secret": "new"}, {"domain": "b.com"}]
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `docker compose run --rm django python manage.py test agents.tests.test_api_tool -v 2`
Expected: ERROR `ModuleNotFoundError: No module named 'agents.api_tool'`

- [ ] **Step 4: Implement** — `backend/agents/api_tool.py`:

```python
"""
Generic HTTP tool for the reply agent.

Businesses enable it per custom instruction with an allowed-domain list; the LLM
builds URL/body, this module enforces the safety rails and injects secrets.
"""
import ipaddress
import logging
import socket
import time
from typing import Callable, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

MAX_CALLS = 5
TIMEOUT = 10
MAX_BYTES = 20_000


def collect_api_domains(instructions: list[dict]) -> list[dict]:
	"""Union of allowed domains from enabled instructions. Input is newest first; first domain wins."""
	merged: dict[str, dict] = {}
	for inst in instructions:
		if not inst.get("api_tool_enabled"):
			continue
		for d in inst.get("api_domains") or []:
			merged.setdefault(d["domain"], d)
	return list(merged.values())


def _match_domain(host: str, domains: list[dict]) -> Optional[dict]:
	for d in domains:
		if host == d["domain"] or host.endswith("." + d["domain"]):
			return d
	return None


def _is_public_host(host: str) -> bool:
	"""True only if every address the host resolves to is public (blocks SSRF to internal networks)."""
	# ponytail: requests re-resolves, so DNS rebinding can slip between checks; pin the IP if that matters
	try:
		infos = socket.getaddrinfo(host, None)
	except (socket.gaierror, UnicodeError):
		return False
	for info in infos:
		ip = ipaddress.ip_address(info[4][0].split("%")[0])
		if not ip.is_global or ip.is_multicast:
			return False
	return bool(infos)


def build_call_api_tool(domains: list[dict], log: list) -> Callable[..., str]:
	calls = 0

	def call_api(method: str, url: str, body: Optional[dict] = None) -> str:
		nonlocal calls
		if calls >= MAX_CALLS:
			return "ERROR: API call limit reached for this reply"
		calls += 1

		method = (method or "").upper()
		if method not in ("GET", "POST"):
			return "ERROR: only GET and POST are allowed"

		parts = urlsplit(url or "")
		host = (parts.hostname or "").rstrip(".")
		if parts.scheme not in ("http", "https") or not host:
			return "ERROR: URL must start with http:// or https://"
		domain = _match_domain(host, domains)
		if not domain:
			return f"ERROR: {host} is not an allowed domain"
		if not _is_public_host(host):
			return f"ERROR: {host} cannot be reached"

		headers = {}
		if domain.get("auth_header") and domain.get("secret"):
			headers[domain["auth_header"]] = domain["secret"]

		entry = {"method": method, "url": url}
		start = time.monotonic()
		try:
			resp = requests.request(
				method, url,
				headers=headers,
				json=body if method == "POST" else None,
				timeout=TIMEOUT,
				allow_redirects=False,
				stream=True,
			)
			try:
				raw = resp.raw.read(MAX_BYTES + 1, decode_content=True) or b""
			finally:
				resp.close()
			entry["status"] = resp.status_code
		except requests.Timeout:
			entry["error"] = "timeout"
			return "ERROR: timeout"
		except requests.RequestException as e:
			entry["error"] = type(e).__name__
			return f"ERROR: request failed ({type(e).__name__})"
		finally:
			entry["ms"] = int((time.monotonic() - start) * 1000)
			log.append(entry)
			logger.info(f"[Tool: call_api] {method} {url} -> {entry.get('status', entry.get('error'))} in {entry['ms']}ms")

		text = raw[:MAX_BYTES].decode(resp.encoding or "utf-8", errors="replace")
		if len(raw) > MAX_BYTES:
			text += "\n[truncated]"
		return f"HTTP {resp.status_code}\n{text}"

	allowed = ", ".join(d["domain"] for d in domains)
	call_api.__doc__ = (
		"Call an external business API with GET or POST and return the HTTP status and response body.\n"
		f"Only these domains (and their subdomains) are allowed: {allowed}.\n"
		"Follow the custom instructions for which URL, method and body fields to use. "
		"Authentication is added automatically; never put keys in the URL or body.\n"
		"Args:\n"
		"\tmethod: GET or POST\n"
		"\turl: full http(s) URL\n"
		"\tbody: JSON object to send with POST (ignored for GET)"
	)
	return call_api
```

- [ ] **Step 5: Run tests, verify pass**

Run: `docker compose run --rm django python manage.py test agents.tests.test_api_tool -v 2`
Expected: 15 tests, OK.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/api_tool.py backend/agents/tests/__init__.py backend/agents/tests/test_api_tool.py
git commit -m "feat(agents): add call_api tool with domain allowlist and SSRF guard"
```

---

### Task 2: Instruction model fields + config validation

**Files:**
- Modify: `backend/knowledge/models.py` (class `CustomInstruction`, after `message` field ~line 231)
- Create: `backend/knowledge/migrations/0010_custominstruction_api_tool.py` (generated)
- Create: `backend/knowledge/api_tool_config.py`
- Test: `backend/knowledge/tests/test_api_tool_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Model fields `CustomInstruction.api_tool_enabled: bool`, `CustomInstruction.api_domains: list[dict]`.
  - `class ApiConfigError(Exception)`
  - `apply_api_config(data: Mapping, current_enabled: bool = False, current_domains: Optional[list] = None) -> tuple[bool, list[dict]]` — raises `ApiConfigError`. Keys read from `data`: `api_tool_enabled`, `api_domains` (each optional; missing = keep current).
  - `public_api_domains(domains: list[dict]) -> list[dict]` — `[{"domain", "auth_header", "has_secret"}]`.

- [ ] **Step 1: Write failing tests** — `backend/knowledge/tests/test_api_tool_config.py`:

```python
from django.test import SimpleTestCase

from knowledge.api_tool_config import ApiConfigError, apply_api_config, public_api_domains


class ApplyApiConfigTest(SimpleTestCase):
	def test_normalizes_domain_from_url(self):
		enabled, domains = apply_api_config({
			"api_tool_enabled": True,
			"api_domains": [{"domain": "https://API.Shop.com:8443/orders?x=1", "auth_header": "X-Key", "secret": "k"}],
		})
		assert enabled is True
		assert domains == [{"domain": "api.shop.com", "auth_header": "X-Key", "secret": "k"}]

	def test_enabled_without_domains_rejected(self):
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_tool_enabled": True, "api_domains": []})

	def test_invalid_domains_rejected(self):
		for bad in ("127.0.0.1", "localhost", "", "[::1]"):
			with self.assertRaises(ApiConfigError, msg=bad):
				apply_api_config({"api_domains": [{"domain": bad}]})

	def test_bad_header_and_secret_without_header_rejected(self):
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_domains": [{"domain": "shop.com", "auth_header": "Bad Header", "secret": "k"}]})
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_domains": [{"domain": "shop.com", "secret": "k"}]})

	def test_too_many_and_duplicate_domains_rejected(self):
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_domains": [{"domain": f"d{i}.com"} for i in range(11)]})
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_domains": [{"domain": "shop.com"}, {"domain": "SHOP.com"}]})

	def test_blank_secret_keeps_existing(self):
		current = [{"domain": "shop.com", "auth_header": "X-Key", "secret": "old"}]
		_, domains = apply_api_config(
			{"api_domains": [{"domain": "shop.com", "auth_header": "X-Key", "secret": ""}]},
			current_enabled=True, current_domains=current,
		)
		assert domains[0]["secret"] == "old"

	def test_removing_header_clears_secret(self):
		current = [{"domain": "shop.com", "auth_header": "X-Key", "secret": "old"}]
		_, domains = apply_api_config({"api_domains": [{"domain": "shop.com"}]}, current_domains=current)
		assert domains == [{"domain": "shop.com"}]

	def test_missing_keys_keep_current(self):
		current = [{"domain": "shop.com"}]
		assert apply_api_config({"label": "x"}, True, current) == (True, current)

	def test_public_output_hides_secret(self):
		out = public_api_domains([{"domain": "shop.com", "auth_header": "X-Key", "secret": "k"}, {"domain": "b.com"}])
		assert out == [
			{"domain": "shop.com", "auth_header": "X-Key", "has_secret": True},
			{"domain": "b.com", "auth_header": "", "has_secret": False},
		]
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test knowledge.tests.test_api_tool_config -v 2`
Expected: ERROR `No module named 'knowledge.api_tool_config'`

- [ ] **Step 3: Add model fields** — in `CustomInstruction`, after `message = models.TextField()`:

```python
	# Generic API tool (agents/api_tool.py). api_domains: [{"domain", "auth_header"?, "secret"?}]
	api_tool_enabled = models.BooleanField(default=False)
	api_domains = models.JSONField(default=list, blank=True)
```

- [ ] **Step 4: Generate migration**

Run: `docker compose run --rm django python manage.py makemigrations knowledge -n custominstruction_api_tool`
Expected: creates `knowledge/migrations/0010_custominstruction_api_tool.py` with two `AddField` operations.

- [ ] **Step 5: Implement** — `backend/knowledge/api_tool_config.py`:

```python
"""Validation and safe output for CustomInstruction API-tool config."""
import ipaddress
import re
from typing import Mapping, Optional
from urllib.parse import urlsplit

MAX_DOMAINS = 10
_HEADER_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class ApiConfigError(Exception):
	pass


def _is_ip(host: str) -> bool:
	try:
		ipaddress.ip_address(host)
		return True
	except ValueError:
		return False


def _normalize_domain(value: str) -> str:
	value = (value or "").strip()
	try:
		host = (urlsplit(value if "//" in value else "//" + value).hostname or "").rstrip(".")
	except ValueError:
		host = ""
	if not host or "." not in host or _is_ip(host):
		raise ApiConfigError(f"Invalid domain: {value!r}. Use a domain name like shop.com")
	return host


def _clean_domains(incoming, existing: list) -> list[dict]:
	if not isinstance(incoming, list):
		raise ApiConfigError("api_domains must be a list")
	if len(incoming) > MAX_DOMAINS:
		raise ApiConfigError(f"At most {MAX_DOMAINS} domains are allowed")
	old_secrets = {d.get("domain"): d.get("secret", "") for d in existing}
	cleaned, seen = [], set()
	for item in incoming:
		if not isinstance(item, dict):
			raise ApiConfigError("Each domain must be an object")
		domain = _normalize_domain(item.get("domain", ""))
		if domain in seen:
			raise ApiConfigError(f"Duplicate domain: {domain}")
		seen.add(domain)
		header = (item.get("auth_header") or "").strip()
		secret = (item.get("secret") or "").strip()
		if header and not _HEADER_RE.match(header):
			raise ApiConfigError(f"Invalid header name: {header!r}")
		if secret and not header:
			raise ApiConfigError(f"Auth header is required when a secret is set ({domain})")
		if header and not secret:
			secret = old_secrets.get(domain, "")  # blank secret on edit = keep stored one
		entry = {"domain": domain}
		if header:
			entry["auth_header"] = header
		if secret:
			entry["secret"] = secret
		cleaned.append(entry)
	return cleaned


def apply_api_config(data: Mapping, current_enabled: bool = False, current_domains: Optional[list] = None) -> tuple[bool, list[dict]]:
	"""Merge request data over current config. Missing keys keep current values."""
	current_domains = list(current_domains or [])
	enabled = bool(data.get("api_tool_enabled", current_enabled))
	domains = _clean_domains(data["api_domains"], current_domains) if "api_domains" in data else current_domains
	if enabled and not domains:
		raise ApiConfigError("Add at least one allowed domain to enable API calls")
	return enabled, domains


def public_api_domains(domains: list[dict]) -> list[dict]:
	return [
		{"domain": d["domain"], "auth_header": d.get("auth_header", ""), "has_secret": bool(d.get("secret"))}
		for d in domains or []
	]
```

- [ ] **Step 6: Run, verify pass**

Run: `docker compose run --rm django python manage.py test knowledge.tests.test_api_tool_config -v 2`
Expected: 9 tests, OK.

- [ ] **Step 7: Commit**

```bash
git add backend/knowledge/models.py backend/knowledge/migrations/0010_custominstruction_api_tool.py backend/knowledge/api_tool_config.py backend/knowledge/tests/test_api_tool_config.py
git commit -m "feat(knowledge): add API tool config to custom instructions"
```

---

### Task 3: Instruction API endpoints expose and accept API config

**Files:**
- Modify: `backend/knowledge/views.py` — `CustomInstructionListCreateView.get/post`, `CustomInstructionDetailView.get/patch`, `CustomInstructionResolveView.get` (each hand-builds the same instruction dict)
- Test: `backend/knowledge/tests/test_instruction_api_tool.py`

**Interfaces:**
- Consumes: `apply_api_config`, `public_api_domains`, `ApiConfigError` from Task 2.
- Produces: instruction JSON gains `api_tool_enabled: bool`, `api_domains: [{domain, auth_header, has_secret}]`. POST `/api/kb/instructions/` and PATCH `/api/kb/instructions/<id>/` accept `api_tool_enabled` and `api_domains: [{domain, auth_header?, secret?}]`; invalid config → 400 `{"success": false, "error": {"code": "invalid_api_config", "message": ...}}`.

- [ ] **Step 1: Write failing tests** — `backend/knowledge/tests/test_instruction_api_tool.py`:

```python
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import CustomInstruction

URL = "/api/kb/instructions/"


class InstructionApiToolTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="a@x.com", email="a@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-api-tool")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user)

	def _create(self, **extra):
		return self.client.post(URL, {
			"project_id": str(self.project.id), "label": "Orders", "message": "Use GET https://shop.com/o/{id}", **extra,
		}, format="json")

	def test_create_stores_secret_but_never_returns_it(self):
		resp = self._create(api_tool_enabled=True, api_domains=[{"domain": "shop.com", "auth_header": "X-Key", "secret": "k1"}])
		assert resp.status_code == 201, resp.content
		inst = resp.json()["data"]["instruction"]
		assert inst["api_tool_enabled"] is True
		assert inst["api_domains"] == [{"domain": "shop.com", "auth_header": "X-Key", "has_secret": True}]
		assert b"k1" not in resp.content
		assert CustomInstruction.objects.get(id=inst["id"]).api_domains[0]["secret"] == "k1"

		listing = self.client.get(URL, {"project_id": str(self.project.id)})
		assert b"k1" not in listing.content
		assert listing.json()["data"]["instructions"][0]["api_domains"][0]["has_secret"] is True

	def test_patch_blank_secret_keeps_it(self):
		inst_id = self._create(api_tool_enabled=True, api_domains=[{"domain": "shop.com", "auth_header": "X-Key", "secret": "k1"}]).json()["data"]["instruction"]["id"]
		resp = self.client.patch(f"{URL}{inst_id}/", {
			"label": "Orders v2", "api_domains": [{"domain": "shop.com", "auth_header": "X-Key", "secret": ""}],
		}, format="json")
		assert resp.status_code == 200, resp.content
		assert CustomInstruction.objects.get(id=inst_id).api_domains[0]["secret"] == "k1"

	def test_invalid_config_returns_400(self):
		assert self._create(api_tool_enabled=True, api_domains=[]).json()["error"]["code"] == "invalid_api_config"
		resp = self._create(api_domains=[{"domain": "127.0.0.1"}])
		assert resp.status_code == 400

	def test_default_is_disabled(self):
		inst = self._create().json()["data"]["instruction"]
		assert inst["api_tool_enabled"] is False and inst["api_domains"] == []
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test knowledge.tests.test_instruction_api_tool -v 2`
Expected: FAIL — `KeyError: 'api_tool_enabled'` / missing `invalid_api_config`.

- [ ] **Step 3: Add helper + imports** in `backend/knowledge/views.py`. Next to the other module imports add:

```python
from .api_tool_config import ApiConfigError, apply_api_config, public_api_domains
```

and above `class CustomInstructionListCreateView` add:

```python
def _instruction_to_dict(inst: CustomInstruction) -> dict:
    return {
        "id": str(inst.id),
        "label": inst.label,
        "message": inst.message,
        "knowledge_base_id": str(inst.knowledge_base_id) if inst.knowledge_base_id else None,
        "knowledge_base_name": inst.knowledge_base.name if inst.knowledge_base else None,
        "api_tool_enabled": inst.api_tool_enabled,
        "api_domains": public_api_domains(inst.api_domains),
        "created_at": inst.created_at.isoformat(),
        "updated_at": inst.updated_at.isoformat(),
    }


def _api_config_error(e: ApiConfigError) -> Response:
    return Response(
        {"success": False, "error": {"code": "invalid_api_config", "message": str(e)}},
        status=status.HTTP_400_BAD_REQUEST
    )
```

- [ ] **Step 4: Replace the five hand-built dicts** with `_instruction_to_dict(...)`:
  - list GET: `instructions = [_instruction_to_dict(inst) for inst in qs.order_by('-created_at')]`
  - create POST response: `"instruction": _instruction_to_dict(instruction)`
  - detail GET response: `"instruction": _instruction_to_dict(instruction)`
  - PATCH response: `"instruction": _instruction_to_dict(instruction)`
  - resolve GET: `result = [_instruction_to_dict(inst) for inst in instructions]`

- [ ] **Step 5: Accept config on create** — in `CustomInstructionListCreateView.post`, after the knowledge-base validation block and before `CustomInstruction.objects.create(`:

```python
            try:
                api_tool_enabled, api_domains = apply_api_config(request.data)
            except ApiConfigError as e:
                return _api_config_error(e)
```

and pass `api_tool_enabled=api_tool_enabled, api_domains=api_domains` to `CustomInstruction.objects.create(...)`.

- [ ] **Step 6: Accept config on update** — in `CustomInstructionDetailView.patch`, after the `message` update and before `instruction.save()`:

```python
            try:
                instruction.api_tool_enabled, instruction.api_domains = apply_api_config(
                    request.data, instruction.api_tool_enabled, instruction.api_domains
                )
            except ApiConfigError as e:
                return _api_config_error(e)
```

- [ ] **Step 7: Run, verify pass**

Run: `docker compose run --rm django python manage.py test knowledge.tests.test_instruction_api_tool knowledge.tests.test_api_tool_config -v 2`
Expected: 13 tests, OK.

- [ ] **Step 8: Commit**

```bash
git add backend/knowledge/views.py backend/knowledge/tests/test_instruction_api_tool.py
git commit -m "feat(knowledge): expose API tool config on instruction endpoints"
```

---

### Task 4: Wire the tool into the reply pipeline

**Files:**
- Modify: `backend/agents/rag.py` (`Context` dataclass ~line 9; `_build_tools_from_context` before `return tools`)
- Modify: `backend/channels_app/tasks.py` (`get_effective_instructions`, `generate_agent_reply`, both tasks' `create_outbound_message` calls, FB meta update)
- Test: `backend/agents/tests/test_api_tool.py` (append one test class)

**Interfaces:**
- Consumes: `build_call_api_tool`, `collect_api_domains` (Task 1); model fields (Task 2).
- Produces:
  - `Context.api_domains: Optional[List[dict]]`, `Context.api_call_log: List[dict]`.
  - `generate_agent_reply(msg, knowledge_base) -> tuple[str, list[dict]]` (reply text, API call log).
  - Outbound `Message.meta["api_calls"]` present when the bot called any API.

- [ ] **Step 1: Write failing test** — append to `backend/agents/tests/test_api_tool.py`:

```python
class BuildToolsWiringTest(SimpleTestCase):
	def test_call_api_only_when_domains_configured(self):
		from agents.rag import Context, ToolDeps, _build_tools_from_context

		names = lambda ctx: [t.__name__ for t in _build_tools_from_context(ctx, ToolDeps())]
		assert "call_api" not in names(Context())
		ctx = Context(api_domains=[{"domain": "shop.com"}])
		assert "call_api" in names(ctx)
		assert ctx.api_call_log == []
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test agents.tests.test_api_tool.BuildToolsWiringTest -v 2`
Expected: FAIL — `TypeError: Context.__init__() got an unexpected keyword argument 'api_domains'`

- [ ] **Step 3: Update `rag.py`**

Change the import line `from dataclasses import dataclass` to:

```python
from dataclasses import dataclass, field
```

add after `from .vector_store import rerank_docs`:

```python
from .api_tool import build_call_api_tool
```

extend `Context`:

```python
@dataclass
class Context:
	knowledge_base_id: Optional[str] = None
	vector_collection: Optional[str] = None
	custom_instructions: Optional[List[dict]] = None  # List of {'label': str, 'message': str}
	api_domains: Optional[List[dict]] = None  # enables call_api tool when non-empty
	api_call_log: List[dict] = field(default_factory=list)  # filled by call_api during the reply
```

in `_build_tools_from_context`, immediately before the final `return tools`:

```python
	if context.api_domains:
		tools.append(build_call_api_tool(context.api_domains, context.api_call_log))
```

- [ ] **Step 4: Run, verify pass**

Run: `docker compose run --rm django python manage.py test agents.tests.test_api_tool -v 2`
Expected: 16 tests, OK.

- [ ] **Step 5: Update `channels_app/tasks.py`**

Import: add `from agents.api_tool import collect_api_domains` next to the other `agents.` imports.

In `get_effective_instructions`, both list comprehensions become:

```python
[{'label': inst.label, 'message': inst.message, 'api_tool_enabled': inst.api_tool_enabled, 'api_domains': inst.api_domains} for inst in kb_instructions]
```

(and the same with `project_instructions`). Update its docstring "Returns" line to mention the two new keys.

In `generate_agent_reply`, build `Context` with the domains and return the log:

```python
	context = Context(
		knowledge_base_id=str(knowledge_base.id),
		vector_collection=KnowledgeBase.get_vector_collection_name(project, knowledge_base.name),
		custom_instructions=custom_instructions,
		api_domains=collect_api_domains(custom_instructions),
	)
```

change its return type annotation to `-> tuple[str, list]`, its docstring to `"""Run the RAG agent for `msg`. Returns (reply text, API call log). DB reads only; call outside any transaction."""`, and its last line to:

```python
	return reply_text, context.api_call_log
```

In `handle_incoming_message`, replace the generate/except block with:

```python
	api_calls: list = []
	try:
		reply_text, api_calls = generate_agent_reply(msg, knowledge_base)
	except Exception as exc:
		if self.request.retries < self.max_retries:
			logger.warning(f"Reply generation failed for {msg.id}, retrying: {exc}")
			raise self.retry(exc=exc)
		logger.error(f"Reply generation failed for {msg.id} after retries: {exc}", exc_info=True)
		reply_text = ""
	reply_text = reply_text or FALLBACK_REPLY
```

pass `meta={"api_calls": api_calls} if api_calls else {},` to its `create_outbound_message(...)`, and change `out_msg.meta = response  # Store full response in meta` to:

```python
		out_msg.meta = {**out_msg.meta, **response}  # keep api_calls, add Facebook send response
```

In `handle_ui_message`: `reply_text, api_calls = generate_agent_reply(msg, msg.conversation.knowledge_base)` and pass `meta={"api_calls": api_calls} if api_calls else {},` to its `create_outbound_message(...)`.

- [ ] **Step 6: Run the reply-pipeline tests**

Run: `docker compose run --rm django python manage.py test agents.tests.test_api_tool channels_app.tests.test_reply_history -v 2`
Expected: all OK.

- [ ] **Step 7: Commit**

```bash
git add backend/agents/rag.py backend/channels_app/tasks.py backend/agents/tests/test_api_tool.py
git commit -m "feat(reply): enable call_api tool from effective instructions, log calls in meta"
```

---

### Task 5: Frontend — API access editor in instruction dialogs

**Files:**
- Modify: `frontend/lib/api.ts` (type `CustomInstruction` ~line 317; `customInstructionApi.create`/`update` data types)
- Create: `frontend/components/knowledge/ApiAccessEditor.tsx`
- Modify: `frontend/app/(dashboard)/knowledge/page.tsx` (state ~lines 58-59, `handleUseTemplate`, `handleAddInstruction`, `handleEditClick`, `handleUpdateInstruction`, both dialogs after the Message `Textarea`, instruction card badge ~line 989)

**Interfaces:**
- Consumes: endpoint contract from Task 3.
- Produces: `ApiDomain` type; `ApiAccessEditor` component; `toApiDomainsPayload(domains: ApiDomain[]): ApiDomain[]`.

- [ ] **Step 1: Types in `frontend/lib/api.ts`** — above `export type CustomInstruction` add:

```ts
export type ApiDomain = {
	domain: string;
	auth_header?: string;
	has_secret?: boolean; // from server only
	secret?: string; // to server only; blank keeps the stored secret
};
```

add to `CustomInstruction`:

```ts
	api_tool_enabled: boolean;
	api_domains: ApiDomain[];
```

extend the `create` data param with `api_tool_enabled?: boolean; api_domains?: ApiDomain[];` and the `update` data param type to `{ label?: string; message?: string; api_tool_enabled?: boolean; api_domains?: ApiDomain[] }`.

- [ ] **Step 2: Create `frontend/components/knowledge/ApiAccessEditor.tsx`**

```tsx
"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ApiDomain } from "@/lib/api";

export type ApiAccessValue = { api_tool_enabled: boolean; api_domains: ApiDomain[] };

function hostnameOf(value: string): string {
	const v = value.trim();
	try {
		return new URL(v.includes("://") ? v : `https://${v}`).hostname;
	} catch {
		return v.toLowerCase();
	}
}

/** Drop empty rows, reduce pasted URLs to hostnames, never send has_secret or blank secrets. */
export function toApiDomainsPayload(domains: ApiDomain[]): ApiDomain[] {
	return domains
		.filter((d) => d.domain.trim())
		.map((d) => ({
			domain: hostnameOf(d.domain),
			...(d.auth_header?.trim() ? { auth_header: d.auth_header.trim() } : {}),
			...(d.secret?.trim() ? { secret: d.secret.trim() } : {}),
		}));
}

const EXAMPLE = (domain: string) =>
	`When the customer asks about an order, ask for their order number, then call GET https://${domain}/orders/{order_number} and summarize the status in one or two sentences. If the call fails, apologize and offer to connect them with a person.`;

export function ApiAccessEditor({
	value,
	message,
	onChange,
	onInsertExample,
}: {
	value: ApiAccessValue;
	message: string;
	onChange: (value: ApiAccessValue) => void;
	onInsertExample: (text: string) => void;
}) {
	const { api_tool_enabled: enabled, api_domains: domains } = value;
	const setDomains = (next: ApiDomain[]) => onChange({ ...value, api_domains: next });
	const update = (i: number, patch: Partial<ApiDomain>) =>
		setDomains(domains.map((d, j) => (j === i ? { ...d, ...patch } : d)));

	const insecure = domains.filter(
		(d) => (d.secret || d.has_secret) && d.domain.trim() && message.includes(`http://${hostnameOf(d.domain)}`)
	);

	return (
		<details className="rounded-md border p-3" open={enabled}>
			<summary className="cursor-pointer text-sm font-medium flex items-center justify-between">
				<span>API access</span>
				<label className="flex items-center gap-2 text-sm font-normal" onClick={(e) => e.stopPropagation()}>
					<input
						type="checkbox"
						checked={enabled}
						onChange={(e) =>
							onChange({
								api_tool_enabled: e.target.checked,
								api_domains: e.target.checked && domains.length === 0 ? [{ domain: "" }] : domains,
							})
						}
					/>
					Enable API calls
				</label>
			</summary>

			<div className="mt-3 space-y-3">
				<p className="text-xs text-muted-foreground">
					The bot can make GET/POST calls only to these domains (and their subdomains). Use a low-privilege API
					key. Describe which URLs and fields to use in the message above.
				</p>

				{domains.map((d, i) => (
					<div key={i} className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_1fr_auto] gap-2 items-end">
						<div className="space-y-1">
							<Label className="text-xs">Domain</Label>
							<Input
								placeholder="shop.com"
								value={d.domain}
								onChange={(e) => update(i, { domain: e.target.value })}
								onBlur={(e) => e.target.value && update(i, { domain: hostnameOf(e.target.value) })}
							/>
						</div>
						<div className="space-y-1">
							<Label className="text-xs">Auth header (optional)</Label>
							<Input
								placeholder="Authorization"
								value={d.auth_header ?? ""}
								onChange={(e) => update(i, { auth_header: e.target.value })}
							/>
						</div>
						<div className="space-y-1">
							<Label className="text-xs">Secret (optional)</Label>
							<Input
								type="password"
								autoComplete="new-password"
								placeholder={d.has_secret ? "(set) leave blank to keep" : "Bearer sk_..."}
								value={d.secret ?? ""}
								onChange={(e) => update(i, { secret: e.target.value })}
							/>
						</div>
						<Button
							type="button"
							variant="ghost"
							size="icon"
							aria-label={`Remove ${d.domain || "domain"}`}
							onClick={() => setDomains(domains.filter((_, j) => j !== i))}
						>
							<Trash2 className="h-4 w-4" />
						</Button>
					</div>
				))}

				<div className="flex flex-wrap gap-2">
					<Button type="button" variant="outline" size="sm" onClick={() => setDomains([...domains, { domain: "" }])}>
						<Plus className="mr-1 h-4 w-4" /> Add domain
					</Button>
					<Button
						type="button"
						variant="link"
						size="sm"
						onClick={() => onInsertExample(EXAMPLE(hostnameOf(domains[0]?.domain || "shop.com")))}
					>
						Insert example
					</Button>
				</div>

				{insecure.length > 0 && (
					<p className="text-xs text-amber-600 dark:text-amber-400">
						Warning: {insecure.map((d) => hostnameOf(d.domain)).join(", ")} is used over http://, so the secret
						and customer data are sent unencrypted. Prefer https://.
					</p>
				)}
			</div>
		</details>
	);
}
```

- [ ] **Step 3: Wire state in `page.tsx`**

Update the `@/lib/api` import to also import `type ApiDomain`, and add:

```tsx
import { ApiAccessEditor, toApiDomainsPayload } from "@/components/knowledge/ApiAccessEditor";
```

Above `export default function KnowledgePage()` add:

```tsx
const EMPTY_INSTRUCTION = { label: "", message: "", api_tool_enabled: false, api_domains: [] as ApiDomain[] };
```

Replace every `{ label: "", message: "" }` in the file with `EMPTY_INSTRUCTION` (state initializers on lines ~58-59 and every reset in handlers/dialogs).

`handleUseTemplate`: `setNewInstruction({ ...EMPTY_INSTRUCTION, label: template.label, message: template.message });`

`handleEditClick`:

```tsx
		setEditInstruction({
			label: instruction.label,
			message: instruction.message,
			api_tool_enabled: instruction.api_tool_enabled ?? false,
			api_domains: (instruction.api_domains ?? []).map((d) => ({ ...d, secret: "" })),
		});
```

- [ ] **Step 4: Validation + payload in save handlers**

In `handleAddInstruction`, after the label/message check:

```tsx
		const newDomains = toApiDomainsPayload(newInstruction.api_domains);
		if (newInstruction.api_tool_enabled && newDomains.length === 0) {
			setAlert({ type: "error", message: "Add at least one allowed domain to enable API calls" });
			return;
		}
```

and add to the `customInstructionApi.create({...})` payload:

```tsx
				api_tool_enabled: newInstruction.api_tool_enabled,
				api_domains: newDomains,
```

In `handleUpdateInstruction`, the same check using `editInstruction` (variable `editDomains`), and add to the `customInstructionApi.update(...)` payload:

```tsx
				api_tool_enabled: editInstruction.api_tool_enabled,
				api_domains: editDomains,
```

- [ ] **Step 5: Render the editor in both dialogs** — directly after the closing `</div>` of each Message `Textarea` block.

Add dialog:

```tsx
									<ApiAccessEditor
										value={newInstruction}
										message={newInstruction.message}
										onChange={(v) => setNewInstruction((prev) => ({ ...prev, ...v }))}
										onInsertExample={(text) =>
											setNewInstruction((prev) => ({ ...prev, message: prev.message ? `${prev.message}\n\n${text}` : text }))
										}
									/>
```

Edit dialog: same with `editInstruction` / `setEditInstruction`. Also give the edit `<DialogContent>` the same classes as the add dialog: `className="max-w-3xl max-h-[90vh] overflow-y-auto"` (the editor makes it taller).

- [ ] **Step 6: Badge on instruction cards** — after `<Badge variant="outline">{inst.label}</Badge>`:

```tsx
												{inst.api_tool_enabled && <Badge variant="secondary">API</Badge>}
```

- [ ] **Step 7: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 8: Manual check** (stack up: `docker compose up -d` + `cd frontend && npm run dev`)
  1. Add instruction, tick **Enable API calls**, leave domain blank → error "Add at least one allowed domain…".
  2. Paste `https://Shop.com/orders?x=1` into domain, blur → shows `shop.com`. Add header `X-Key` + secret, save → card shows **API** badge.
  3. Edit it → secret field empty with placeholder "(set) leave blank to keep"; change label only, save; reopen → still "(set)".
  4. Put `http://shop.com/...` in the message → amber warning appears.
  5. Browser devtools → Network: list/get responses contain no secret value.
  6. Chat in a UI conversation using that KB with an instruction pointing at a public test API (e.g. `https://httpbin.org/get` with domain `httpbin.org`) → reply uses the data; outbound message `meta.api_calls` has the entry.

- [ ] **Step 9: Commit**

```bash
git add frontend/lib/api.ts frontend/components/knowledge/ApiAccessEditor.tsx "frontend/app/(dashboard)/knowledge/page.tsx"
git commit -m "feat(ui): API access editor for custom instructions"
```
