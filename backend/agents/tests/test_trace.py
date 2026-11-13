import os
from unittest import mock

from django.test import SimpleTestCase
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from agents.api_tool import build_call_api_tool
from agents.tests.test_api_tool import _addr, _resp
from agents.rag import Context, ToolDeps, _build_tools_from_context, _tool_name, generate_reply


class FakeRetriever:
	def invoke(self, query):
		return [
			Document(page_content="Open 9-5", metadata={"page_url": "https://shop.com/hours", "page_title": "Hours"}),
			Document(page_content="Refunds in 30 days", metadata={"document_filename": "policy.pdf"}),
		]


class TraceTest(SimpleTestCase):
	def test_retrieval_records_sources(self):
		ctx = Context(vector_collection="vc")
		with mock.patch("agents.rag.rerank_docs", side_effect=lambda q, docs, **kw: docs):
			tools = {t.__name__: t for t in _build_tools_from_context(ctx, ToolDeps(retriever_factory=lambda c: FakeRetriever()))}
			tools["retrieve_business_context"]("hours")
		assert ctx.trace["sources"] == ["https://shop.com/hours", "policy.pdf"]

	def test_generate_reply_records_model_tokens_tools_and_latency(self):
		result = {"messages": [
			HumanMessage("hours?"),
			AIMessage("", tool_calls=[{"name": "retrieve_business_context", "args": {"query": "hours"}, "id": "c1"}],
				usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}),
			ToolMessage("Open 9-5", tool_call_id="c1"),
			AIMessage("We're open 9-5.", usage_metadata={"input_tokens": 150, "output_tokens": 20, "total_tokens": 170},
				response_metadata={"model_name": "gemini-3.8-flash"}),
		]}
		agent = mock.Mock()
		agent.invoke.return_value = result
		ctx = Context()
		with mock.patch("agents.rag.create_basic_agent", return_value=agent):
			assert generate_reply("hours?", ctx) == "We're open 9-5."
		t = ctx.trace
		assert t["model"] == "gemini-3.8-flash"
		assert (t["tokens_in"], t["tokens_out"]) == (250, 30)
		assert t["tool_calls"] == [{"name": "retrieve_business_context", "args": {"query": "hours"}}]
		assert isinstance(t["ms"], int)


def _names(tools):
	return {_tool_name(t) for t in tools}


def _order_tools():
	# Built the way production builds them: LangChain StructuredTool objects, which expose .name and no __name__
	def fn(**kwargs):
		return "ok"
	return [StructuredTool.from_function(func=fn, name=n, description=n) for n in ("find_products", "update_draft", "submit_order")]


API_DOMAINS = [{"domain": "shop.example"}]


class VisionTurnTest(SimpleTestCase):
	def test_multimodal_content_blocks(self):
		agent = mock.Mock()
		agent.invoke.return_value = {"messages": [AIMessage("I can see it.")]}
		ctx = Context(vision_images=["data:image/jpeg;base64,AAA"])
		history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}]
		with mock.patch("agents.rag.create_basic_agent", return_value=agent) as make:
			assert generate_reply("what is this?", ctx, history=history) == "I can see it."
		messages = agent.invoke.call_args.args[0]["messages"]
		assert messages[0] == {"role": "user", "content": "earlier"} and messages[1] == {"role": "assistant", "content": "ok"}
		assert messages[-1]["content"] == [
			{"type": "text", "text": "what is this?"},
			{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
		]
		assert make.call_args.kwargs.get("route") == "vision"
		assert ctx.trace["vision_images"] == 1

	def test_vision_turn_keeps_read_only_tools(self):
		ctx = Context(vision_images=["data:image/jpeg;base64,AAA"], api_domains=API_DOMAINS, order_tools=_order_tools())
		tools = _build_tools_from_context(ctx, ToolDeps(), retrievers=[])
		assert _names(tools) == {"retrieve_business_context", "escalate_to_human", "call_api", "find_products"}

	def test_text_turn_keeps_call_api_and_all_order_tools(self):
		ctx = Context(api_domains=API_DOMAINS, order_tools=_order_tools())
		tools = _build_tools_from_context(ctx, ToolDeps(), retrievers=[])
		assert _names(tools) == {"retrieve_business_context", "escalate_to_human", "call_api", "find_products", "update_draft", "submit_order"}

	@mock.patch("agents.api_tool.socket.getaddrinfo", return_value=_addr("93.184.216.34"))
	@mock.patch("agents.api_tool.requests.request")
	def test_vision_turn_call_api_allows_get_and_blocks_post(self, req, _dns):
		req.return_value = _resp()
		log = []
		call_api = build_call_api_tool(API_DOMAINS, log, read_only=True)
		call_api("GET", "https://shop.example/orders/1")
		assert req.call_count == 1
		out = call_api("POST", "https://shop.example/orders", {"item": 1})
		assert out.startswith("ERROR: only GET is allowed while looking at a photo")
		assert req.call_count == 1 and not any(entry.get("method") == "POST" for entry in log)

	def test_tool_name_accessor_supports_langchain_tools(self):
		find, _, submit = _order_tools()
		assert _tool_name(find) == "find_products" and not hasattr(find, "__name__")
		assert _tool_name(submit) == "submit_order"

	def test_tracing_enabled_drops_images(self):
		agent = mock.Mock()
		agent.invoke.return_value = {"messages": [AIMessage("text only")]}
		ctx = Context(vision_images=["data:image/jpeg;base64,AAA"])
		with mock.patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}), mock.patch("agents.rag.create_basic_agent", return_value=agent):
			generate_reply("what is this?", ctx)
		assert agent.invoke.call_args.args[0]["messages"][-1]["content"] == "what is this?"
		assert ctx.vision_images == [] and ctx.trace["vision_skipped"] == "tracing"
