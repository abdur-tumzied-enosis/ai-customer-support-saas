from unittest import mock

from django.test import SimpleTestCase
from langchain_core.embeddings import DeterministicFakeEmbedding
from qdrant_client import QdrantClient, models

from agents import vector_store


class HybridSearchTest(SimpleTestCase):
	"""In-memory Qdrant, real BM25, fake dense vectors: only the keyword side can find the SKU."""

	def setUp(self):
		client = QdrantClient(":memory:")
		patches = [
			mock.patch.object(vector_store, "get_qdrant_client", return_value=client),
			mock.patch.object(vector_store, "get_default_embeddings", return_value=DeterministicFakeEmbedding(size=8)),
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

	def test_pre_hybrid_collection_still_searches_dense(self):
		self.client.create_collection("old", vectors_config=models.VectorParams(size=8, distance=models.Distance.COSINE))
		assert not vector_store._is_hybrid(self.client, "old")
		assert vector_store.get_retriever("old", k=5).invoke("anything") == []
