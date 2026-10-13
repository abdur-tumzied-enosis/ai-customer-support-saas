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
