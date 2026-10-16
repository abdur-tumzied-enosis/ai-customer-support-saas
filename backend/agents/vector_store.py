import os
import threading
from typing import Optional, List
from django.conf import settings
from qdrant_client import QdrantClient, models
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode

import logging
logger = logging.getLogger(__name__)

_reranker = None
_reranker_lock = threading.Lock()
_embeddings = None
_embeddings_lock = threading.Lock()
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


def get_default_embeddings():
	# multilingual-e5-base: a Bengali question finds English docs and vice versa (bge-base-en-v1.5 found 0/8
	# Bengali→English in our eval, e5 7/8 top-1 at the same speed; bge-m3 8/8 but 2.3x slower).
	# e5 is trained with "query: " / "passage: " prefixes; drop them if EMBEDDING_MODEL is not an e5 model.
	# NOTE: changing this model requires re-indexing all existing Qdrant collections.
	global _embeddings
	if _embeddings is None:
		with _embeddings_lock:
			if _embeddings is None:  # re-check after acquiring lock
				model_name = getattr(settings, 'EMBEDDING_MODEL', 'intfloat/multilingual-e5-base')
				logger.info(f"Load pretrained SentenceTransformer: {model_name}; it should only be load once when celery get start")
				_embeddings = HuggingFaceEmbeddings(
					model_name=model_name,
					encode_kwargs={"normalize_embeddings": True, "prompt": "passage: "},
					query_encode_kwargs={"normalize_embeddings": True, "prompt": "query: "},
				)
	return _embeddings


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


def rerank_docs(query: str, docs: List[Document], top_k: int = 4, min_score: Optional[float] = None) -> List[Document]:
	"""
	Rerank retrieved documents using a cross-encoder for higher precision.
	Drops docs scoring below `min_score` (unrelated text), so an off-topic query can return nothing.
	Falls back to the original order (no cutoff) if the reranker is unavailable.
	"""
	global _reranker
	if not docs:
		return docs
	try:
		if _reranker is None:
			with _reranker_lock:
				if _reranker is None:  # re-check after acquiring lock
					from sentence_transformers import CrossEncoder
					# Multilingual (Bengali etc.), same speed as the English ms-marco-MiniLM-L-6-v2.
					# ponytail: BAAI/bge-reranker-v2-m3 ranks better but took ~3.5s per query on CPU; use it with a GPU.
					reranker_model = getattr(settings, 'RERANKER_MODEL', 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')
					_reranker = CrossEncoder(reranker_model)
		pairs = [(query, doc.page_content) for doc in docs]
		scores = _reranker.predict(pairs)
		ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
		return [doc for score, doc in ranked[:top_k] if min_score is None or score >= min_score]
	except Exception as e:
		logger.warning(f"Reranker failed, using original order: {e}")
		return docs[:top_k]


def get_retriever(collection_name: str, k: int = 10):
	if not collection_name:
		return None
	client = get_qdrant_client()
	if not _collection_exists(client, collection_name):
		logger.debug(f"Collection '{collection_name}' does not exist, returning None retriever")
		return None
	if _is_hybrid(client, collection_name):
		return _store(client, collection_name).as_retriever(search_kwargs={"k": k})
	# Created before hybrid search: dense only until `manage.py reindex_vectors` rebuilds it
	logger.warning(f"Collection '{collection_name}' has no sparse vectors; dense-only search. Run reindex_vectors.")
	store = QdrantVectorStore(client=client, collection_name=collection_name, embedding=get_default_embeddings())
	return store.as_retriever(search_kwargs={"k": k})


def _store(client: QdrantClient, collection_name: str) -> QdrantVectorStore:
	"""Hybrid store: dense + BM25 candidates fused with RRF (the reranker then orders them)."""
	return QdrantVectorStore(
		client=client,
		collection_name=collection_name,
		embedding=get_default_embeddings(),
		sparse_embedding=get_sparse_embeddings(),
		sparse_vector_name=SPARSE_VECTOR_NAME,
		retrieval_mode=RetrievalMode.HYBRID,
	)


def _is_hybrid(client: QdrantClient, collection_name: str) -> bool:
	return SPARSE_VECTOR_NAME in (client.get_collection(collection_name).config.params.sparse_vectors or {})


def _create_collection(client: QdrantClient, collection_name: str) -> None:
	# Not QdrantVectorStore.construct_instance: it creates the sparse vector without the IDF modifier BM25 needs
	size = len(get_default_embeddings().embed_query("dimension probe"))
	client.create_collection(
		collection_name=collection_name,
		vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
		sparse_vectors_config={SPARSE_VECTOR_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)},
	)


def _collection_exists(client: QdrantClient, collection_name: str) -> bool:
	return client.collection_exists(collection_name)


def add_texts_to_collection(collection_name: str, texts: list[str], metadatas: Optional[list[dict]] = None, ids: Optional[list[str]] = None) -> list[str]:
	"""
	Add a batch of texts to the Qdrant collection, creating it if missing.
	Returns the list of inserted IDs.
	"""
	client = get_qdrant_client()
	metadatas = metadatas or [{} for _ in texts]
	if not _collection_exists(client, collection_name):
		_create_collection(client, collection_name)
	# A pre-hybrid collection rejects sparse vectors: QdrantVectorStore raises, ingestion fails until reindex_vectors
	inserted_ids = _store(client, collection_name).add_texts(texts=texts, metadatas=metadatas, ids=ids or None)
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