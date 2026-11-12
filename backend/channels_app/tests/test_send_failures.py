from unittest import mock

import requests
from django.test import SimpleTestCase
from urllib3.exceptions import MaxRetryError, NewConnectionError

from agents.api_tool import build_call_api_tool
from channels_app.facebook_client import send_text_message, send_whatsapp_text
from core.http import never_sent


def refused():
	"""What requests raises when the connection is refused or DNS fails."""
	return requests.ConnectionError(MaxRetryError(None, "/", NewConnectionError(None, "refused")))


class NeverSentTest(SimpleTestCase):
	def test_connect_failures_never_reached_the_server(self):
		assert never_sent(requests.ConnectTimeout()) and never_sent(refused())

	def test_anything_that_may_have_reached_it_is_not(self):
		assert not never_sent(requests.ReadTimeout())
		assert not never_sent(requests.ConnectionError("Connection aborted."))  # dropped mid-response
		assert not never_sent(ValueError())


class SendClientsTest(SimpleTestCase):
	@mock.patch("channels_app.facebook_client.requests.post")
	def test_whatsapp_connect_failure_is_a_plain_failure_but_a_read_timeout_is_unknown(self, post):
		for error, unknown in ((requests.ConnectTimeout(), False), (refused(), False), (requests.ReadTimeout(), True)):
			post.side_effect = error
			assert send_whatsapp_text("tok", "pn", "880", "hi").get("unknown", False) is unknown

	@mock.patch("channels_app.facebook_client.requests.post")
	def test_messenger_connect_failure_is_a_plain_failure_but_a_read_timeout_is_unknown(self, post):
		for error, expected in ((requests.ConnectTimeout(), {}), (refused(), {}), (requests.ReadTimeout(), {"unknown": True})):
			post.side_effect = error
			assert send_text_message("tok", "psid", "hi") == expected


class CallApiConnectTest(SimpleTestCase):
	def call(self, error):
		tool = build_call_api_tool([{"domain": "shop.example"}], [])
		with mock.patch("agents.api_tool._is_public_host", return_value=True), \
				mock.patch("agents.api_tool.requests.request", side_effect=error):
			return tool("POST", "https://shop.example/order", {})

	def test_connect_failure_is_not_treated_as_maybe_done(self):
		assert self.call(requests.ConnectTimeout()) == "ERROR: could not connect"
		assert self.call(refused()) == "ERROR: could not connect"

	def test_read_timeout_and_dropped_connection_still_are(self):
		assert self.call(requests.ReadTimeout()) == "ERROR: timeout"
		assert self.call(requests.ConnectionError("Connection aborted.")).startswith("ERROR: request failed")
