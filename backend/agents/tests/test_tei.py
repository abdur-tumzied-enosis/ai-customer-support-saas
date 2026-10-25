from unittest import mock

from django.test import SimpleTestCase, override_settings
from langchain_core.documents import Document

from agents import vector_store


def _response(payload):
	r = mock.Mock()
	r.json.return_value = payload
	return r


class TEIClientTest(SimpleTestCase):
	def test_embeddings_use_e5_prompts_and_batch(self):
		texts = [f"t{i}" for i in range(vector_store.TEI_BATCH + 1)]
		with mock.patch("agents.vector_store.requests.post", side_effect=lambda url, json, timeout: _response([[0.1]] * len(json["inputs"]))) as post:
			emb = vector_store.TEIEmbeddings("http://embeddings/")
			assert len(emb.embed_documents(texts)) == len(texts)
			assert emb.embed_query("hi") == [0.1]
		batches = [c.kwargs["json"]["inputs"] for c in post.call_args_list]
		assert [len(b) for b in batches] == [vector_store.TEI_BATCH, 1, 1]
		assert batches[0][0] == "passage: t0" and batches[2] == ["query: hi"]
		assert post.call_args.args[0] == "http://embeddings/embed"

	@override_settings(RERANKER_URL="http://reranker")
	def test_rerank_maps_scores_back_to_docs_and_applies_cutoff(self):
		docs = [Document(page_content=t) for t in ("off-topic", "best", "ok")]
		# TEI returns results sorted by score, not in input order
		ranked = [{"index": 1, "score": 9.0}, {"index": 2, "score": 1.0}, {"index": 0, "score": -6.0}]
		with mock.patch("agents.vector_store.requests.post", return_value=_response(ranked)) as post:
			out = vector_store.rerank_docs("q", docs, top_k=3, min_score=-4.5)
		assert [d.page_content for d in out] == ["best", "ok"]
		assert post.call_args.kwargs["json"]["raw_scores"] is True
