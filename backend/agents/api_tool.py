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
