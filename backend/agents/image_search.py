from io import BytesIO

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from PIL import Image, ImageOps
from qdrant_client import models

from agents.vector_store import _create_collection, get_default_embeddings, get_qdrant_client

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_SEARCH_TOP_K = 3
# Stored photo variants: `display` replaces the upload, `thumb` sits next to it
DISPLAY_EDGE = 1600
THUMB_EDGE = 320
WEBP_QUALITY = 80

IMAGE_KIND = models.FieldCondition(key="kind", match=models.MatchValue(value="image"))  # photo points share the KB collection with text

_FORMATS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp"}  # MPO: multi-picture JPEG from phones


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


def _webp(img: Image.Image, edge: int) -> bytes:
	img = img.copy()
	img.thumbnail((edge, edge))  # keeps aspect, never upscales
	buf = BytesIO()
	img.save(buf, "WEBP", quality=WEBP_QUALITY)  # no exif= : metadata is dropped
	return buf.getvalue()


def make_variants(data: bytes, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, bytes]:
	"""(display, thumb) WebP versions of an uploaded photo, upright and without metadata. Raises NotAnImage."""
	img = load_image(data, max_bytes)
	return _webp(img, DISPLAY_EDGE), _webp(img, THUMB_EDGE)


def image_format(data: bytes) -> str:
	"""'jpg' | 'png' | 'webp'. Call after load_image has accepted the data."""
	return _FORMATS[Image.open(BytesIO(data)).format]


def upsert_image(collection: str, image_id: str, vector: list[float], product_id: str, usage: list | None = None) -> None:
	"""usage, if given, receives the Embedded of the dimension probe when this call creates the collection."""
	client = get_qdrant_client()
	if not client.collection_exists(collection):
		try:
			embedding = get_default_embeddings()
			try:
				_create_collection(client, collection, embedding)  # hybrid-capable: text ingestion shares this collection
			finally:
				if usage is not None:
					usage.extend(embedding.usage)
		except Exception:
			if not client.collection_exists(collection):  # else another worker created it first
				raise
	client.upsert(collection, points=[models.PointStruct(
		id=image_id, vector=vector, payload={"kind": "image", "product_id": product_id, "image_id": image_id},
	)])


def search_products(collection: str, vector: list[float]) -> list[tuple[str, float]]:
	"""Best-matching products (one hit per product), best first, above IMAGE_SEARCH_MIN_SCORE."""
	client = get_qdrant_client()
	if not client.collection_exists(collection):
		return []
	res = client.query_points_groups(
		collection, query=vector, group_by="product_id", limit=IMAGE_SEARCH_TOP_K, group_size=1,
		query_filter=models.Filter(must=[IMAGE_KIND]), score_threshold=settings.IMAGE_SEARCH_MIN_SCORE,
	)
	return [(g.id, g.hits[0].score) for g in res.groups if g.hits]


def delete_images(collection: str, image_ids: list[str]) -> None:
	client = get_qdrant_client()
	if image_ids and client.collection_exists(collection):
		client.delete(collection, points_selector=models.PointIdsList(points=image_ids))
