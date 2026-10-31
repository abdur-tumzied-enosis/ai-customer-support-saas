import os
import threading
from io import BytesIO

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from PIL import Image, ImageOps
from qdrant_client import models

from agents.vector_store import get_qdrant_client

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_SEARCH_TOP_K = 3

_FORMATS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp"}  # MPO: multi-picture JPEG from phones

_model = None
_model_lock = threading.Lock()


class NotAnImage(ValueError):
	pass


def load_image(data: bytes, max_bytes: int = MAX_IMAGE_BYTES) -> Image.Image:
	"""Decode an uploaded JPEG/PNG/WEBP as upright RGB (EXIF rotation applied, transparency on white);
	anything else, or over max_bytes, raises NotAnImage."""
	if len(data) > max_bytes:
		raise NotAnImage("image too large")
	try:
		img = Image.open(BytesIO(data))
		if img.format not in _FORMATS:
			raise NotAnImage("unsupported image format")
		if img.width * img.height > MAX_IMAGE_PIXELS:
			raise NotAnImage("image has too many pixels")
		img = ImageOps.exif_transpose(img)  # phone photos are stored sideways with an orientation tag
		if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
			rgba = img.convert("RGBA")
			background = Image.new("RGB", rgba.size, (255, 255, 255))
			background.paste(rgba, mask=rgba.getchannel("A"))
			return background
		return img.convert("RGB")
	except (NotAnImage, SoftTimeLimitExceeded):  # SoftTimeLimitExceeded is an Exception too: let the reply task see it
		raise
	except Exception as e:  # Pillow raises SyntaxError, ValueError, struct.error, zlib.error, EOFError... on corrupt data
		raise NotAnImage("not a valid image") from e


def image_format(data: bytes) -> str:
	"""'jpg' | 'png' | 'webp'. Call after load_image has accepted the data."""
	return _FORMATS[Image.open(BytesIO(data)).format]


def _get_model():
	# ponytail: in-process CLIP model; move to a model server like TEI if worker memory hurts.
	global _model
	if _model is None:
		with _model_lock:
			if _model is None:
				from fastembed import ImageEmbedding
				# Baked into the image (Dockerfile); without local_files_only fastembed asks the hub first and fails offline.
				# threads=1: every worker process loads its own copy (~650 MB), and ONNX's default of one thread per core
				# across many processes oversubscribes the CPU.
				_model = ImageEmbedding(
					settings.IMAGE_EMBEDDING_MODEL, threads=1, local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1",
				)
	return _model


def embed_image(img: Image.Image) -> list[float]:
	return list(_get_model().embed([img]))[0].tolist()


def upsert_image(collection: str, image_id: str, vector: list[float], product_id: str) -> None:
	client = get_qdrant_client()
	if not client.collection_exists(collection):
		try:
			client.create_collection(
				collection_name=collection,
				vectors_config=models.VectorParams(size=len(vector), distance=models.Distance.COSINE),
			)
		except Exception:
			if not client.collection_exists(collection):  # else another worker created it first
				raise
	client.upsert(collection, points=[models.PointStruct(
		id=image_id, vector=vector, payload={"product_id": product_id, "image_id": image_id},
	)])


def search_products(collection: str, vector: list[float]) -> list[tuple[str, float]]:
	"""Best-matching products (one hit per product), best first, above IMAGE_SEARCH_MIN_SCORE."""
	client = get_qdrant_client()
	if not client.collection_exists(collection):
		return []
	res = client.query_points_groups(
		collection, query=vector, group_by="product_id", limit=IMAGE_SEARCH_TOP_K, group_size=1,
		score_threshold=settings.IMAGE_SEARCH_MIN_SCORE,
	)
	return [(g.id, g.hits[0].score) for g in res.groups if g.hits]


def delete_images(collection: str, image_ids: list[str]) -> None:
	client = get_qdrant_client()
	if image_ids and client.collection_exists(collection):
		client.delete(collection, points_selector=models.PointIdsList(points=image_ids))
