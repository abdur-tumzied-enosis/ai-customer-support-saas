import os
import threading
from typing import Optional, List
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from qdrant_client import QdrantClient, models
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode

from usage.utils import record_model_usage
from . import embeddings_api
from .embeddings_api import EmbeddingError, rerank_scores

import logging
logger = logging.getLogger(__name__)

_sparse = None
_sparse_lock = threading.Lock()

SPARSE_VECTOR_NAME = "langchain-sparse"  # langchain_qdrant's default name


def get_qdrant_client() -> QdrantClient:
	"""
	Get a Qdrant client instance with optional API key authentication.
	Supports both development (no auth) and production (with API key) setups.
	"""
	api_key = getattr(settings, 'QDRANT_API_KEY', None)
	if api_key:
		return QdrantClient(url=settings.QDRANT_URL, api_key=api_key)
	return QdrantClient(url=settings.QDRANT_URL)


def get_default_embeddings() -> "OpenRouterEmbeddings":
	# cohere/embed-5-fast through OpenRouter (agents/embeddings_api.py).
	# NOTE: changing this model requires re-indexing all existing Qdrant collections.
	if not settings.OPENROUTER_API_KEY:
		raise ImproperlyConfigured("OPENROUTER_API_KEY is not set; embeddings need it")
	return OpenRouterEmbeddings()


class OpenRouterEmbeddings(Embeddings):
	"""Cohere embeddings via OpenRouter. Each call's Embedded (tokens, cost) lands on last_usage and in usage."""

	def __init__(self):
		self.last_usage = None
		self.usage = []

	def _embed(self, texts: list[str], kind: str) -> list[list[float]]:
		out = embeddings_api.embed_texts(texts, kind)
		self.last_usage = out
		self.usage.append(out)
		return out.vectors

	def embed_documents(self, texts: list[str]) -> list[list[float]]:
		return self._embed(texts, "document")

	def embed_query(self, text: str) -> list[float]:
		return self._embed([text], "query")[0]


def get_sparse_embeddings() -> FastEmbedSparse:
	# BM25 keyword vectors: dense search alone misses exact SKUs, prices and names.
	# The collection's IDF modifier supplies the BM25 term weights (see _create_collection).
	global _sparse
	if _sparse is None:
		with _sparse_lock:
			if _sparse is None:
				# Baked into the image (Dockerfile); without local_files_only fastembed asks the hub first and fails offline
				_sparse = FastEmbedSparse(model_name="Qdrant/bm25", local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1")
	return _sparse


def rerank_docs(
	query: str, docs: List[Document], top_k: int = 4, min_score: Optional[float] = None, project_id: Optional[str] = None
) -> List[Document]:
	"""
	Rerank retrieved documents using a cross-encoder for higher precision.
	Drops docs scoring below `min_score` (unrelated text), so an off-topic query can return nothing.
	Falls back to the original order (no cutoff) if the reranker is unavailable.
	"""
	if not docs:
		return docs
	try:
		res = rerank_scores(query, [doc.page_content for doc in docs])
		if project_id:
			record_model_usage(project_id, "rerank", tokens=res.tokens, cost_usd=res.cost_usd, model=res.model)
		ranked = sorted(zip(res.scores, docs), key=lambda x: x[0], reverse=True)
		return [doc for score, doc in ranked[:top_k] if min_score is None or score >= min_score]
	except EmbeddingError as e:
		logger.warning(f"Reranker failed, using original order: {e}")
		return docs[:top_k]


# Text retrieval never returns photo points (they carry a top-level `kind`; text points have none)
NOT_IMAGE = models.Filter(must_not=[models.FieldCondition(key="kind", match=models.MatchValue(value="image"))])


def get_retriever(collection_name: str, k: int = 10):
	if not collection_name:
		return None
	client = get_qdrant_client()
	if not _collection_exists(client, collection_name):
		logger.debug(f"Collection '{collection_name}' does not exist, returning None retriever")
		return None
	if _is_hybrid(client, collection_name):
		return _store(client, collection_name).as_retriever(search_kwargs={"k": k, "filter": NOT_IMAGE})
	# Created before hybrid search: dense only until `manage.py reindex_vectors` rebuilds it
	logger.warning(f"Collection '{collection_name}' has no sparse vectors; dense-only search. Run reindex_vectors.")
	store = QdrantVectorStore(client=client, collection_name=collection_name, embedding=get_default_embeddings())
	return store.as_retriever(search_kwargs={"k": k, "filter": NOT_IMAGE})


def _store(client: QdrantClient, collection_name: str, embedding: Optional[Embeddings] = None) -> QdrantVectorStore:
	"""Hybrid store: dense + BM25 candidates fused with RRF (the reranker then orders them)."""
	return QdrantVectorStore(
		client=client,
		collection_name=collection_name,
		embedding=embedding or get_default_embeddings(),
		sparse_embedding=get_sparse_embeddings(),
		sparse_vector_name=SPARSE_VECTOR_NAME,
		retrieval_mode=RetrievalMode.HYBRID,
	)


def _is_hybrid(client: QdrantClient, collection_name: str) -> bool:
	return SPARSE_VECTOR_NAME in (client.get_collection(collection_name).config.params.sparse_vectors or {})


def _create_collection(client: QdrantClient, collection_name: str, embedding: Optional[Embeddings] = None) -> None:
	# Not QdrantVectorStore.construct_instance: it creates the sparse vector without the IDF modifier BM25 needs
	size = len((embedding or get_default_embeddings()).embed_query("dimension probe"))
	client.create_collection(
		collection_name=collection_name,
		vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
		sparse_vectors_config={SPARSE_VECTOR_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)},
	)


def _collection_exists(client: QdrantClient, collection_name: str) -> bool:
	return client.collection_exists(collection_name)


def add_texts_to_collection(collection_name: str, texts: list[str], metadatas: Optional[list[dict]] = None, ids: Optional[list[str]] = None, usage: Optional[list] = None) -> list[str]:
	"""
	Add a batch of texts to the Qdrant collection, creating it if missing.
	Returns the list of inserted IDs. Each embedding call's Embedded (tokens, cost) is appended to `usage`.
	"""
	client = get_qdrant_client()
	metadatas = metadatas or [{} for _ in texts]
	embedding = get_default_embeddings()
	if not _collection_exists(client, collection_name):
		_create_collection(client, collection_name, embedding)  # the probe's usage lands in embedding.usage too
	# A pre-hybrid collection rejects sparse vectors: QdrantVectorStore raises, ingestion fails until reindex_vectors
	inserted_ids = _store(client, collection_name, embedding).add_texts(texts=texts, metadatas=metadatas, ids=ids or None)
	if usage is not None:
		usage.extend(embedding.usage)
	return [str(x) for x in inserted_ids]


def delete_collection(collection_name: str) -> None:
	client = get_qdrant_client()
	if _collection_exists(client, collection_name):
		client.delete_collection(collection_name)


def add_text_to_collection(collection_name: str, text: str, metadata: Optional[dict] = None, id: Optional[str] = None) -> list[str]:
	"""
	Add a single text to the collection (convenience wrapper). Returns list of inserted IDs.
	"""
	return add_texts_to_collection(collection_name, [text], [metadata or {}], [id] if id else None)


def delete_vectors_from_collection(collection_name: str, vector_ids: list[str]) -> bool:
	"""
	Delete vectors from a Qdrant collection by their IDs.
	Returns True if successful, False otherwise.
	"""
	try:
		from qdrant_client.models import PointIdsList
		from typing import cast, Any
		
		client = get_qdrant_client()
		
		# Check if collection exists
		if not _collection_exists(client, collection_name):
			return False
		
		# Delete vectors by IDs using PointIdsList
		# Qdrant accepts string IDs, so we cast to satisfy type checker
		client.delete(
			collection_name=collection_name,
			points_selector=PointIdsList(points=cast(Any, vector_ids))
		)
		return True
	except Exception as e:
		import logging
		logger = logging.getLogger(__name__)
		logger.error(f"Failed to delete vectors from collection {collection_name}: {str(e)}")
		return False