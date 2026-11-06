import logging
import uuid
from time import monotonic

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from agents.api_tool import _is_public_host, _parse_target
from agents.image_search import NotAnImage, make_variants
from core.storage import delete_keys, thumb_key
from .models import MAX_IMAGES_PER_PRODUCT, MAX_PRODUCT_IMAGE_BYTES, ProductImage, product_image_path
from .tasks import embed_product_image

logger = logging.getLogger(__name__)

TIMEOUT = 10
IMAGE_DOWNLOAD_DEADLINE_SECONDS = 30  # wall clock per image: TIMEOUT only bounds each socket read
CHUNK = 64 * 1024
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class ImageFetchError(Exception):
	"""Message is stored on ProductImage.error: keep it short, never include the URL or headers."""


def _check_url(url: str) -> None:
	try:
		scheme, host = _parse_target(url)
	except ValueError:
		raise ImageFetchError("invalid_url")
	if scheme not in ("http", "https") or not host:
		raise ImageFetchError("invalid_url")
	if not _is_public_host(host):
		raise ImageFetchError("blocked_host")


def _read_capped(resp) -> bytes:
	declared = resp.headers.get("Content-Length", "")
	if declared.isdigit() and int(declared) > MAX_PRODUCT_IMAGE_BYTES:
		raise ImageFetchError("too_large")
	buf = bytearray()
	started = monotonic()
	for chunk in resp.iter_content(chunk_size=CHUNK):
		buf.extend(chunk)
		if len(buf) > MAX_PRODUCT_IMAGE_BYTES:  # stop reading as soon as the cap is passed
			raise ImageFetchError("too_large")
		if monotonic() - started > IMAGE_DOWNLOAD_DEADLINE_SECONDS:
			raise ImageFetchError("too_slow")
	return bytes(buf)


def fetch_image(url: str) -> tuple[bytes, str]:
	"""Download one public image. Returns (bytes, extension). Raises ImageFetchError."""
	_check_url(url)
	try:
		resp = requests.get(url, stream=True, allow_redirects=False, timeout=TIMEOUT)
	except requests.RequestException:
		raise ImageFetchError("download_failed")
	try:
		if resp.status_code != 200:  # redirects included: a 3xx could point at an internal host
			raise ImageFetchError("redirect_not_followed" if 300 <= resp.status_code < 400 else "http_error")
		ext = EXTENSIONS.get(resp.headers.get("Content-Type", "").split(";")[0].strip().lower())
		if ext is None:
			raise ImageFetchError("bad_content_type")
		try:
			return _read_capped(resp), ext
		except requests.RequestException:
			raise ImageFetchError("download_failed")
	finally:
		resp.close()


def attach_product_images(product, urls: list[str]) -> None:
	"""Download urls into ProductImage rows. One bad image is marked failed and never stops the rest or the product."""
	project = product.kb.project
	slots = MAX_IMAGES_PER_PRODUCT - product.images.count()
	for url in list(dict.fromkeys(urls))[: max(slots, 0)]:
		image_id = uuid.uuid4()
		image = ProductImage.objects.create(id=image_id, product=product, status="pending")
		name = None
		try:
			data, _ext = fetch_image(url)
			display, thumb = make_variants(data, MAX_PRODUCT_IMAGE_BYTES)  # same variant step as uploads; rejects non-images
			name = default_storage.save(product_image_path(product.kb, str(image_id), "webp"), ContentFile(display))
			default_storage.save(thumb_key(name), ContentFile(thumb))
		except (ImageFetchError, NotAnImage) as exc:
			error = "not_an_image" if isinstance(exc, NotAnImage) else str(exc)
			ProductImage.objects.filter(id=image_id).update(status="failed", error=error)
			continue
		except Exception:
			logger.exception("Storing downloaded product image failed")
			if name:
				delete_keys(name, thumb_key(name))
			ProductImage.objects.filter(id=image_id).update(status="failed", error="storage_failed")
			continue
		ProductImage.objects.filter(id=image_id).update(file=name, status="ready", error="")
		if project.image_search_on:
			transaction.on_commit(lambda i=str(image_id): embed_product_image.delay(i))
