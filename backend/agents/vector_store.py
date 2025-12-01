from typing import Optional
from django.conf import settings
from qdrant_client import QdrantClient
from langchain_community.vectorstores import Qdrant
try:
	from langchain_huggingface import HuggingFaceEmbeddings  # preferred in v1
except Exception:  # pragma: no cover
	# Fallback for environments without langchain-huggingface installed
	from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore


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
	# Small, fast, permissive model suitable for PoC
	return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")


def get_retriever(collection_name: str, k: int = 4):
	if not collection_name:
		return None
	client = get_qdrant_client()
	embeddings = get_default_embeddings()
	store = Qdrant(client=client, collection_name=collection_name, embeddings=embeddings)
	return store.as_retriever(search_kwargs={"k": k})


def _collection_exists(client: QdrantClient, collection_name: str) -> bool:
	try:
		client.get_collection(collection_name)
		return True
	except Exception:
		return False


def add_texts_to_collection(collection_name: str, texts: list[str], metadatas: Optional[list[dict]] = None, ids: Optional[list[str]] = None) -> list[str]:
	"""
	Add a batch of texts to the Qdrant collection, creating it if missing.
	Returns the list of inserted IDs.
	"""
	client = get_qdrant_client()
	embeddings = get_default_embeddings()
	metadatas = metadatas or [{} for _ in texts]
	if ids is None:
		ids = []

	if not _collection_exists(client, collection_name):
		# Create collection on first insert
		api_key = getattr(settings, 'QDRANT_API_KEY', None)
		Qdrant.from_texts(
			texts=texts,
			embedding=embeddings,
			metadatas=metadatas,
			ids=ids if ids else None,
			url=settings.QDRANT_URL,
			api_key=api_key,  # Pass API key if available
			collection_name=collection_name,
		)
		return ids if ids else []

	store = Qdrant(client=client, collection_name=collection_name, embeddings=embeddings)
	inserted_ids = store.add_texts(texts=texts, metadatas=metadatas, ids=ids if ids else None)
	if inserted_ids is None:
		return ids if ids else []
	return [str(x) for x in inserted_ids]


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