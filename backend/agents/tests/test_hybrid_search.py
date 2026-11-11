import types
import uuid
from unittest import mock

from django.test import SimpleTestCase, override_settings
from langchain_core.embeddings import DeterministicFakeEmbedding
from qdrant_client import QdrantClient, models

from agents import image_search, vector_store


class HybridSearchTest(SimpleTestCase):
	"""In-memory Qdrant, real BM25, fake dense vectors: only the keyword side can find the SKU."""

	def setUp(self):
		client = QdrantClient(":memory:")
		patches = [
			mock.patch.object(vector_store, "get_qdrant_client", return_value=client),
			mock.patch.object(image_search, "get_qdrant_client", return_value=client),
			mock.patch.object(vector_store, "get_default_embeddings", return_value=DeterministicFakeEmbedding(size=8)),
			mock.patch.object(image_search, "get_default_embeddings", side_effect=lambda: types.SimpleNamespace(
				usage=[], embed_query=lambda t: [0.0] * 8)),  # upsert_image's dimension probe
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		self.client = client

	def test_exact_sku_is_found_where_dense_misses_it(self):
		texts = [f"General store policy number {i} about returns and delivery" for i in range(20)]
		sku = "The AX-4471 blender costs $89 and ships in two days"
		vector_store.add_texts_to_collection("kb", texts + [sku])
		assert vector_store._is_hybrid(self.client, "kb")
		dense = vector_store.QdrantVectorStore(client=self.client, collection_name="kb", embedding=vector_store.get_default_embeddings())
		assert sku not in [d.page_content for d in dense.similarity_search("AX-4471", k=3)]
		# RRF only gets it into the candidates; the cross-encoder in rag._search orders them
		assert sku in [d.page_content for d in vector_store.get_retriever("kb", k=3).invoke("AX-4471")]

	def test_photo_and_text_share_one_hybrid_collection(self):
		image_search.upsert_image("kb", str(uuid.uuid4()), [1, 0, 0, 0, 0, 0, 0, 0], "pA")  # photo first: creates the collection
		vector_store.add_texts_to_collection("kb", ["The AX-4471 blender costs $89"])
		assert vector_store._is_hybrid(self.client, "kb")

	def test_text_retrieval_skips_photo_points_and_photo_search_skips_text(self):
		vector_store.add_texts_to_collection("kb", ["The AX-4471 blender costs $89"])
		image_search.upsert_image("kb", str(uuid.uuid4()), [1, 0, 0, 0, 0, 0, 0, 0], "pA")
		docs = vector_store.get_retriever("kb", k=10).invoke("blender")
		self.assertEqual([d.page_content for d in docs], ["The AX-4471 blender costs $89"])
		text_vec = vector_store.get_default_embeddings().embed_query("The AX-4471 blender costs $89")
		with override_settings(IMAGE_SEARCH_MIN_SCORE=-1):
			self.assertEqual([p for p, _ in image_search.search_products("kb", text_vec)], ["pA"])

	def test_pre_hybrid_collection_still_searches_dense(self):
		self.client.create_collection("old", vectors_config=models.VectorParams(size=8, distance=models.Distance.COSINE))
		assert not vector_store._is_hybrid(self.client, "old")
		assert vector_store.get_retriever("old", k=5).invoke("anything") == []
