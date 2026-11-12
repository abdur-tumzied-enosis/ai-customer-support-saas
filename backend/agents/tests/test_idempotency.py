import inspect
import uuid
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase

from agents.idempotency import UNCONFIRMED, once
from agents.models import ToolExecution
from agents.rag import Context, ToolDeps, _build_tools_from_context


class OnceTest(TestCase):
	def setUp(self):
		self.context = Context(message_id=str(uuid.uuid4()))
		self.calls = []

	def tool(self, fn=None, **kwargs):
		def create_order(item: str, qty: int = 1) -> str:
			"""Create an order."""
			self.calls.append((item, qty))
			return f"order #{len(self.calls)}"

		return once(self.context, fn or create_order, **kwargs)

	def test_same_call_runs_once_and_returns_the_stored_result(self):
		tool = self.tool()
		assert tool("tea") == "order #1"
		assert tool("tea") == "order #1"  # a retried reply, or the model asking twice
		assert tool("tea", qty=1) == "order #1"  # a default passed explicitly is the same call
		assert self.calls == [("tea", 1)]
		row = ToolExecution.objects.get()
		assert (row.status, row.tool_name, row.result) == ("completed", "create_order", "order #1")
		assert row.completed_at and row.input_payload == {"item": "tea", "qty": 1}

	def test_different_arguments_are_a_different_operation(self):
		tool = self.tool()
		tool("tea")
		tool("coffee")
		tool("tea", qty=2)
		assert len(self.calls) == 3 and ToolExecution.objects.count() == 3

	def test_other_inbound_message_runs_again(self):
		self.tool()("tea")
		self.context.message_id = str(uuid.uuid4())
		self.tool()("tea")
		assert len(self.calls) == 2

	def test_worker_died_mid_tool_is_unknown_not_repeated_and_goes_to_staff(self):
		tool = self.tool()
		ToolExecution.objects.create(  # what the dead run left behind: started, never finished
			message_id=self.context.message_id, tool_name="create_order", status="executing",
			operation_key=__import__("hashlib").sha256(b'create_order:{"item": "tea", "qty": 1}').hexdigest(),
		)
		assert tool("tea") == UNCONFIRMED
		assert self.calls == []
		assert ToolExecution.objects.get().status == "unknown"
		assert self.context.handoff_reason == "unconfirmed action: create_order"
		assert tool("tea") == UNCONFIRMED  # and stays that way on every later retry

	def test_tool_that_fails_before_its_side_effect_is_retried(self):
		def flaky(item: str) -> str:
			self.calls.append(item)
			if len(self.calls) == 1:
				raise RuntimeError("db down")
			return "done"

		tool = self.tool(flaky)
		with self.assertRaises(RuntimeError):
			tool("tea")
		assert ToolExecution.objects.get().status == "failed"
		assert tool("tea") == "done"  # the retried reply runs it
		assert ToolExecution.objects.get().status == "completed" and tool("tea") == "done" and len(self.calls) == 2

	def test_error_result_is_retryable(self):
		results = iter(["ERROR: cannot be reached", "HTTP 200\nok"])
		tool = self.tool(lambda_tool(lambda: next(results)))
		assert tool() == "ERROR: cannot be reached"
		assert ToolExecution.objects.get().status == "failed"
		assert tool() == "HTTP 200\nok"

	def test_side_effect_with_timeout_is_unknown_and_never_repeated(self):
		results = iter(["ERROR: timeout", "HTTP 200\ncreated"])
		tool = self.tool(lambda_tool(lambda: next(results)), unsure=lambda r: r.startswith("ERROR: timeout"))
		assert tool() == UNCONFIRMED
		assert ToolExecution.objects.get().status == "unknown"
		assert self.context.handoff_reason == "unconfirmed action: lambda_tool"
		assert tool() == UNCONFIRMED  # the second result was never asked for
		assert next(results) == "HTTP 200\ncreated"

	def test_two_workers_cannot_both_own_an_operation(self):
		self.tool()("tea")
		row = ToolExecution.objects.get()
		with self.assertRaises(IntegrityError), transaction.atomic():
			ToolExecution.objects.create(message_id=row.message_id, operation_key=row.operation_key, tool_name="x")

	def test_replay_tells_the_reply_pipeline_an_action_happened(self):
		replayed = []
		tool = self.tool(replay=replayed.append)
		tool("tea")
		assert self.context.api_call_log == []  # live call: the tool itself reports
		tool("tea")
		assert replayed == ["order #1"]
		assert self.context.api_call_log == [{"method": "POST", "url": "replayed:create_order"}]

	def test_read_only_calls_are_not_recorded(self):
		tool = self.tool(when=lambda args: args["qty"] > 1)
		tool("tea")
		tool("tea")
		assert len(self.calls) == 2 and ToolExecution.objects.count() == 0

	def test_no_inbound_message_means_no_recording(self):
		self.context.message_id = None  # evals, test chat
		tool = self.tool()
		tool("tea")
		tool("tea")
		assert len(self.calls) == 2 and ToolExecution.objects.count() == 0

	def test_wrapper_keeps_the_signature_and_docstring_the_model_sees(self):
		tool = self.tool()
		assert inspect.signature(tool) == inspect.signature(tool.__wrapped__)
		assert tool.__name__ == "create_order" and tool.__doc__ == "Create an order."


def lambda_tool(fn):
	def lambda_tool() -> str:
		return fn()

	return lambda_tool


class CallApiWiringTest(TestCase):
	def test_gets_are_free_but_a_post_runs_once_per_message(self):
		calls = []

		def call_api(method, url, body=None):
			calls.append(method)
			return "HTTP 200\nok"

		with mock.patch("agents.rag.build_call_api_tool", return_value=call_api):
			context = Context(api_domains=[{"domain": "shop.example"}], message_id=str(uuid.uuid4()))
			tools = _build_tools_from_context(context, ToolDeps(), retrievers=[])
		tool = next(t for t in tools if t.__name__ == "call_api")
		tool("GET", "https://shop.example/a")
		tool("GET", "https://shop.example/a")
		tool("POST", "https://shop.example/order", {"x": 1})
		tool("POST", "https://shop.example/order", {"x": 1})
		assert calls == ["GET", "GET", "POST"]

	def test_a_post_that_timed_out_is_not_sent_again(self):
		results = iter(["ERROR: timeout", "HTTP 200\ncreated"])
		with mock.patch("agents.rag.build_call_api_tool", return_value=lambda method, url, body=None: next(results)):
			context = Context(api_domains=[{"domain": "shop.example"}], message_id=str(uuid.uuid4()))
			tools = _build_tools_from_context(context, ToolDeps(), retrievers=[])
		tool = next(t for t in tools if t.__name__ == "<lambda>")
		assert tool("POST", "https://shop.example/order", {}) == UNCONFIRMED
		assert tool("POST", "https://shop.example/order", {}) == UNCONFIRMED
		assert context.handoff_reason == "unconfirmed action: <lambda>"


class OrderToolsOnceTest(TestCase):
	def test_only_submit_and_cancel_are_recorded(self):
		from orders.tools import build_order_tools

		context = Context(message_id=str(uuid.uuid4()))
		tools = {t.__name__: t for t in build_order_tools(mock.Mock(), mock.Mock(), context)}
		assert {name for name, t in tools.items() if hasattr(t, "__wrapped__")} == {"submit_order", "cancel_order"}
