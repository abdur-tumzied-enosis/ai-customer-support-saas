import socket
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from orders.http import MAX_BYTES, HttpResult, OutboundError, safe_request

PUBLIC_IP = "93.184.216.34"


def _resolve(host, *args, **kwargs):
	ip = PUBLIC_IP if host == "courier.example.com" else host
	return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _response(status=200, body=b"ok"):
	resp = MagicMock(status_code=status)
	resp.iter_content.return_value = iter([body])
	return resp


class SafeRequestTest(SimpleTestCase):
	def setUp(self):
		patcher = patch("agents.api_tool.socket.getaddrinfo", side_effect=_resolve)
		self.resolve = patcher.start()
		self.addCleanup(patcher.stop)
		req = patch("orders.http.requests.request", return_value=_response())
		self.request = req.start()
		self.addCleanup(req.stop)

	def test_action_internal_url_blocked(self):
		for url in ["http://127.0.0.1/x", "http://10.0.0.5/x", "http://169.254.169.254/"]:
			with self.subTest(url=url), self.assertRaises(OutboundError):
				safe_request("GET", url, {})
		self.request.assert_not_called()

	def test_redirect_not_followed(self):
		self.request.return_value = _response(status=302)
		result = safe_request("GET", "https://courier.example.com/x", {})
		self.assertFalse(result.ok)
		self.assertEqual(result.status, 302)
		self.assertIs(self.request.call_args.kwargs["allow_redirects"], False)

	def test_body_capped(self):
		self.request.return_value = _response(body=b"a" * 50_000)
		result = safe_request("GET", "https://courier.example.com/x", {})
		self.assertTrue(result.text.endswith("\n[truncated]"))
		self.assertEqual(len(result.text), MAX_BYTES + len("\n[truncated]"))

	def test_timeout_is_outbound_error(self):
		self.request.side_effect = requests.Timeout("slow")
		with self.assertRaises(OutboundError):
			safe_request("GET", "https://courier.example.com/x", {})

	def test_connection_error_is_outbound_error(self):
		self.request.side_effect = requests.ConnectionError("refused")
		with self.assertRaises(OutboundError):
			safe_request("POST", "https://courier.example.com/x", {}, body="{}")

	def test_non_http_scheme_rejected(self):
		for url in ["file:///etc/passwd", "ftp://courier.example.com/x"]:
			with self.subTest(url=url), self.assertRaises(OutboundError):
				safe_request("GET", url, {})
		self.request.assert_not_called()

	def test_2xx_is_ok(self):
		self.request.return_value = _response(status=201, body=b'{"id":1}')
		result = safe_request("POST", "https://courier.example.com/x", {"X-Key": "k"}, body="{}")
		self.assertEqual(result, HttpResult(status=201, text='{"id":1}', ok=True))
