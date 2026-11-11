from unittest import mock

from django.test import SimpleTestCase
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from agents import vector_store
from agents.embeddings_api import EmbeddingError, Reranked
from agents.rag import Context, ToolDeps, _build_tools_from_context, generate_reply


class Retriever:
	def __init__(self, *texts):
		self.texts = texts

	def invoke(self, query):
		return [Document(page_content=t, metadata={"document_filename": t}) for t in self.texts]


class Broken:
	def invoke(self, query):
		raise ConnectionError("qdrant down")


def fake_rerank_scores(query, texts):
	"""Scores a doc by its leading number: "9 open 9-5" → 9."""
	return Reranked([float(text.split()[0]) for text in texts], 0, None, "cohere/rerank-v3.5")


def deps(kb, web):
	return ToolDeps(retriever_factory=lambda c: kb, retrieved_factory_for_crawler_url_context=lambda: web)


@mock.patch.object(vector_store, "rerank_scores", fake_rerank_scores)
@mock.patch("agents.rag.settings.RERANK_MIN_SCORE", 0.0)
class MergedSearchTest(SimpleTestCase):
	def search(self, kb, web, ctx=None):
		ctx = ctx or Context(vector_collection="vc")
		tools = {t.__name__: t for t in _build_tools_from_context(ctx, deps(kb, web))}
		assert "retrieve_crawler_context" not in tools  # one tool searches both collections
		return tools["retrieve_business_context"]("hours"), ctx

	def test_ranks_both_collections_together_and_drops_unrelated(self):
		out, ctx = self.search(Retriever("2 kb low", "-5 kb unrelated"), Retriever("8 web best", "-9 web unrelated"))
		assert ctx.trace["sources"] == ["8 web best", "2 kb low"]
		assert "unrelated" not in out

	def test_nothing_relevant_says_so(self):
		out, _ = self.search(Retriever("-5 a"), Retriever("-6 b"))
		assert out == "No relevant information found."

	def test_one_collection_down_still_answers_from_the_other(self):
		out, ctx = self.search(Broken(), Retriever("3 web hours"))
		assert "3 web hours" in out and ctx.trace["search_errors"] == ["hours"]

	def test_all_down_is_an_error(self):
		out, _ = self.search(Broken(), Broken())
		assert out.startswith("ERROR: search is unavailable")


@mock.patch.object(vector_store, "rerank_scores", fake_rerank_scores)
@mock.patch("agents.rag.settings.RERANK_MIN_SCORE", 0.0)
class SearchFirstTest(SimpleTestCase):
	def run_reply(self, history=None):
		agent = mock.Mock()
		agent.invoke.return_value = {"messages": [AIMessage("We're open 9-5.")]}
		ctx = Context(vector_collection="vc")
		with mock.patch("agents.rag.create_basic_agent", return_value=agent) as create:
			generate_reply("when?", ctx, deps(Retriever("5 open 9-5"), None), history=history)
		return create.call_args.kwargs["search_results"], ctx

	def test_results_go_in_the_prompt(self):
		results, ctx = self.run_reply()
		assert results == "Query: when?\n\n[Source: 5 open 9-5]\n5 open 9-5"
		assert ctx.trace["search_query"] == "when?"

	def test_follow_up_query_keeps_the_previous_customer_message(self):
		history = [{"role": "user", "content": "is the Main St store open sunday?"}, {"role": "assistant", "content": "No."}]
		_, ctx = self.run_reply(history)
		assert ctx.trace["search_query"] == "is the Main St store open sunday?\nwhen?"


class RerankDocsTest(SimpleTestCase):
	docs = [Document(page_content=t) for t in ("off-topic", "best", "ok")]

	def rerank(self, **kw):
		return [d.page_content for d in vector_store.rerank_docs("q", self.docs, **kw)]

	def test_empty_makes_no_call(self):
		with mock.patch.object(vector_store, "rerank_scores") as call:
			assert vector_store.rerank_docs("q", []) == []
		call.assert_not_called()

	def test_scores_decide_order_and_min_score_drops_low(self):
		with mock.patch.object(vector_store, "rerank_scores", return_value=Reranked([0.01, 0.9, 0.4], 5, None, "m")):
			assert self.rerank(top_k=3) == ["best", "ok", "off-topic"]
			assert self.rerank(top_k=3, min_score=0.1) == ["best", "ok"]

	def test_embedding_error_keeps_original_order(self):
		with mock.patch.object(vector_store, "rerank_scores", side_effect=EmbeddingError("down")):
			assert self.rerank(top_k=2, min_score=0.5) == ["off-topic", "best"]

	def test_books_rerank_usage_when_project_given(self):
		res = Reranked([0.1, 0.9, 0.4], 7, None, "cohere/rerank-v3.5")
		with mock.patch.object(vector_store, "rerank_scores", return_value=res), 				mock.patch.object(vector_store, "record_model_usage") as rec:
			vector_store.rerank_docs("q", self.docs)
			rec.assert_not_called()
			vector_store.rerank_docs("q", self.docs, project_id="p1")
		rec.assert_called_once_with("p1", "rerank", tokens=7, cost_usd=None, model="cohere/rerank-v3.5")
