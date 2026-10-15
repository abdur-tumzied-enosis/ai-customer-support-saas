from unittest import mock

from django.test import SimpleTestCase
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.rag import Context, ToolDeps, _build_tools_from_context, generate_reply


class FakeRetriever:
	def invoke(self, query):
		return [
			Document(page_content="Open 9-5", metadata={"page_url": "https://shop.com/hours", "page_title": "Hours"}),
			Document(page_content="Refunds in 30 days", metadata={"document_filename": "policy.pdf"}),
		]


class TraceTest(SimpleTestCase):
	def test_retrieval_records_sources(self):
		ctx = Context(vector_collection="vc")
		with mock.patch("agents.rag.rerank_docs", side_effect=lambda q, docs, top_k: docs):
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
