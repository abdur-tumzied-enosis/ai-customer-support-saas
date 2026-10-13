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


class BuildToolsWiringTest(SimpleTestCase):
	def test_call_api_only_when_domains_configured(self):
		from agents.rag import Context, ToolDeps, _build_tools_from_context

		names = lambda ctx: [t.__name__ for t in _build_tools_from_context(ctx, ToolDeps())]
		assert "call_api" not in names(Context())
		ctx = Context(api_domains=[{"domain": "shop.com"}])
		assert "call_api" in names(ctx)
		assert ctx.api_call_log == []
