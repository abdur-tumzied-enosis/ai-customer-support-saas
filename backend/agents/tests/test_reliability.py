from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.test import SimpleTestCase
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.llm import fallback_to
from agents.rag import Context, ToolDeps, _build_tools_from_context


class BrokenRetriever:
	def invoke(self, query):
		raise ConnectionError("qdrant down")


class SearchErrorTest(SimpleTestCase):
	def test_retrieval_error_becomes_tool_result(self):
		ctx = Context(vector_collection="vc")
		tools = {t.__name__: t for t in _build_tools_from_context(ctx, ToolDeps(retriever_factory=lambda c: BrokenRetriever()))}
		assert tools["retrieve_business_context"]("hours").startswith("ERROR: search is unavailable")
		assert ctx.trace["search_errors"] == ["retrieve_business_context"]


class FailingModel(FakeListChatModel):
	error: type = RuntimeError

	def _call(self, *args, **kwargs):
		raise self.error("provider down")


class FallbackTest(SimpleTestCase):
	def run_agent(self, primary):
		agent = create_agent(model=primary, tools=[], middleware=[fallback_to(FakeListChatModel(responses=["from backup"]))])
		return agent.invoke({"messages": [{"role": "user", "content": "hi"}]})["messages"][-1].content

	def test_error_retries_on_backup(self):
		assert self.run_agent(FailingModel(responses=["x"])) == "from backup"

	def test_soft_time_limit_is_not_swallowed(self):
		with self.assertRaises(SoftTimeLimitExceeded):
			self.run_agent(FailingModel(responses=["x"], error=SoftTimeLimitExceeded))
