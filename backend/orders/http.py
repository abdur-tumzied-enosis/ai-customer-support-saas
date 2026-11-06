from dataclasses import dataclass

import requests

from agents.api_tool import MAX_BYTES, _is_public_host, _parse_target


@dataclass
class HttpResult:
	status: int | None
	text: str
	ok: bool


class OutboundError(Exception):
	"""Unsafe URL, timeout or connection failure. The message never carries the URL's query or a header value."""


def safe_request(method: str, url: str, headers: dict[str, str], body: str | None = None, timeout: float = 10) -> HttpResult:
	try:
		scheme, host = _parse_target(url)
	except ValueError as e:
		raise OutboundError(str(e))
	if scheme not in ("http", "https") or not host:
		raise OutboundError("URL must start with http:// or https://")
	if not _is_public_host(host):
		raise OutboundError(f"{host} is not a public address")
	try:
		resp = requests.request(
			method, url, headers=headers, data=body, timeout=timeout, allow_redirects=False, stream=True,
		)
		try:
			buf = b""
			for chunk in resp.iter_content(chunk_size=8192):
				buf += chunk
				if len(buf) > MAX_BYTES:
					break
		finally:
			resp.close()
	except requests.RequestException as e:
		raise OutboundError(f"request failed: {type(e).__name__}")
	text = buf[:MAX_BYTES].decode("utf-8", errors="replace")
	if len(buf) > MAX_BYTES:
		text += "\n[truncated]"
	return HttpResult(status=resp.status_code, text=text, ok=200 <= resp.status_code < 300)
