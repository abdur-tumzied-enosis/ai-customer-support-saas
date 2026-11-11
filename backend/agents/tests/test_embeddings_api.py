from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, override_settings
from PIL import Image

from agents import embeddings_api
from agents.embeddings_api import EmbeddingError, embed_image, embed_texts, rerank_scores


def _resp(payload, status=200):
	r = mock.Mock(status_code=status)
	r.json.return_value = payload
	r.text = str(payload)
	return r


def _embed_payload(n):
	return {
		"data": [{"index": i, "embedding": [float(i), 0.5]} for i in reversed(range(n))],
		"usage": {"prompt_tokens": 7, "cost": 0.0000014},
	}


@mock.patch("agents.embeddings_api.requests.post")
class EmbeddingsApiTest(SimpleTestCase):
	def test_embed_texts_query_payload_order_and_usage(self, post):
		post.return_value = _resp(_embed_payload(2))
		out = embed_texts(["a", "b"], "query")
		self.assertEqual(post.call_args.kwargs["json"], {"model": "cohere/embed-5-fast", "input": ["a", "b"], "input_type": "search_query"})
		self.assertEqual(out.vectors, [[0.0, 0.5], [1.0, 0.5]])
		self.assertEqual(out.tokens, 7)
		self.assertEqual(out.cost_usd, Decimal("0.0000014"))
		self.assertEqual(out.model, "cohere/embed-5-fast")

	def test_embed_texts_document_input_type(self, post):
		post.return_value = _resp(_embed_payload(1))
		embed_texts(["a"], "document")
		self.assertEqual(post.call_args.kwargs["json"]["input_type"], "search_document")

	def test_missing_cost_is_none(self, post):
		payload = _embed_payload(1)
		del payload["usage"]["cost"]
		post.return_value = _resp(payload)
		self.assertIsNone(embed_texts(["a"], "query").cost_usd)

	def test_embed_image_sends_jpeg_data_url(self, post):
		post.return_value = _resp(_embed_payload(1))
		embed_image(Image.new("RGB", (4, 4), "red"))
		(item,) = post.call_args.kwargs["json"]["input"]
		(part,) = item["content"]
		self.assertEqual(part["type"], "image_url")
		self.assertTrue(part["image_url"]["url"].startswith("data:image/jpeg;base64,"))

	def test_rerank_scores_in_input_order(self, post):
		post.return_value = _resp({
			"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}],
			"usage": {"total_tokens": 5, "cost": 0.002},
		})
		out = rerank_scores("q", ["x", "y"])
		self.assertEqual(out.scores, [0.1, 0.9])
		self.assertEqual(out.cost_usd, Decimal("0.002"))
		self.assertTrue(post.call_args.args[0].endswith("/rerank"))

	def test_non_200_raises(self, post):
		post.return_value = _resp({"error": "boom"}, status=500)
		with self.assertRaises(EmbeddingError):
			embed_texts(["a"], "query")
		with self.assertRaises(EmbeddingError):
			rerank_scores("q", ["a"])

	def test_malformed_body_raises(self, post):
		post.return_value = _resp({"nope": 1})
		with self.assertRaises(EmbeddingError):
			embed_texts(["a"], "query")

	def test_empty_texts_no_request(self, post):
		out = embed_texts([], "document")
		self.assertEqual((out.vectors, out.tokens, out.cost_usd), ([], 0, None))
		post.assert_not_called()

	def test_eval_sized_batch_returns_one_vector_per_input(self, post):
		post.return_value = _resp(_embed_payload(8))
		self.assertEqual(len(embed_texts([f"q{i}" for i in range(8)], "query").vectors), 8)


class OpenRouterEmbeddingsTest(SimpleTestCase):
	def test_embed_query_uses_query_kind_and_records_usage(self):
		emb = embeddings_api.Embedded([[1.0, 2.0]], 3, Decimal("0.1"), "m")
		with mock.patch("agents.embeddings_api.embed_texts", return_value=emb) as et:
			from agents.vector_store import OpenRouterEmbeddings
			e = OpenRouterEmbeddings()
			self.assertEqual(e.embed_query("x"), [1.0, 2.0])
		et.assert_called_once_with(["x"], "query")
		self.assertIs(e.last_usage, emb)

	def test_embed_documents_uses_document_kind(self):
		emb = embeddings_api.Embedded([[1.0], [2.0]], 3, None, "m")
		with mock.patch("agents.embeddings_api.embed_texts", return_value=emb) as et:
			from agents.vector_store import OpenRouterEmbeddings
			self.assertEqual(OpenRouterEmbeddings().embed_documents(["a", "b"]), [[1.0], [2.0]])
		et.assert_called_once_with(["a", "b"], "document")

	def test_add_texts_to_collection_appends_usage(self):
		from agents import vector_store
		emb = embeddings_api.Embedded([[1.0]], 3, None, "m")
		acc = []
		with mock.patch.object(vector_store, "get_qdrant_client"), 				mock.patch.object(vector_store, "_collection_exists", return_value=True), 				mock.patch.object(vector_store, "get_sparse_embeddings"), 				mock.patch.object(vector_store, "QdrantVectorStore") as qs, 				mock.patch("agents.embeddings_api.embed_texts", return_value=emb), 				override_settings(OPENROUTER_API_KEY="k"):
			qs.return_value.add_texts.side_effect = lambda **kw: (qs.call_args.kwargs["embedding"].embed_documents(["a"]), ["id1"])[1]
			ids = vector_store.add_texts_to_collection("kb", ["a"], usage=acc)
		self.assertEqual(ids, ["id1"])
		self.assertEqual(acc, [emb])


class EmbedUsageBookingTest(SimpleTestCase):
	def test_new_collection_probe_usage_is_collected(self):
		from agents import vector_store
		probe = embeddings_api.Embedded([[1.0, 2.0]], 2, Decimal("0.01"), "m")
		docs = embeddings_api.Embedded([[1.0, 2.0]], 5, Decimal("0.02"), "m")
		acc = []
		with mock.patch.object(vector_store, "get_qdrant_client"), 				mock.patch.object(vector_store, "_collection_exists", return_value=False), 				mock.patch.object(vector_store, "get_sparse_embeddings"), 				mock.patch.object(vector_store, "QdrantVectorStore") as qs, 				mock.patch("agents.embeddings_api.embed_texts", side_effect=[probe, docs]), 				override_settings(OPENROUTER_API_KEY="k"):
			qs.return_value.add_texts.side_effect = lambda **kw: (qs.call_args.kwargs["embedding"].embed_documents(["a"]), ["id1"])[1]
			vector_store.add_texts_to_collection("kb", ["a"], usage=acc)
		self.assertEqual(acc, [probe, docs])

	def test_crawled_document_books_embed_usage(self):
		from knowledge import tasks
		emb = embeddings_api.Embedded([[1.0]], 9, Decimal("0.03"), "m")
		doc = mock.Mock(markdown_content="hello world", page_url="u", page_title="t", crawl_depth=0)
		doc.kb.project_id = "p1"
		with mock.patch.object(tasks, "add_texts_to_collection", side_effect=lambda **kw: (kw["usage"].append(emb), ["i"])[1]), 				mock.patch("usage.utils.record_model_usage") as rec:
			tasks.embed_crawled_document(doc, "web")
		self.assertEqual(rec.call_args.args, ("p1", "embed"))
		self.assertEqual((rec.call_args.kwargs["tokens"], rec.call_args.kwargs["cost_usd"], rec.call_args.kwargs["model"]), (9, Decimal("0.03"), "m"))
