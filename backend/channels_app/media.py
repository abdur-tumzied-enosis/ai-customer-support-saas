"""Fetch customer photos from each channel and store uploaded ones. This is an SSRF boundary: only Meta CDN hosts are fetched.
Failures become MediaError, except celery's SoftTimeLimitExceeded (an Exception subclass), which must reach reply._reply."""
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from celery.exceptions import SoftTimeLimitExceeded
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from agents.image_search import MAX_IMAGE_BYTES, image_format, load_image

from . import tasks
from .facebook_client import GRAPH_API_URL
from .models import Message

META_CDN_SUFFIXES = (".fbcdn.net", ".fbsbx.com", ".cdninstagram.com")
UPLOAD_PREFIXES = ("chat_media/", "widget_uploads/", "chat_uploads/")
TIMEOUT = 10


class MediaError(Exception):
	pass


def is_photo(att: dict) -> bool:
	return att.get("type") == "image" and not (att.get("payload") or {}).get("sticker_id")


def has_photo(msg: Message) -> bool:
	"""A real photo among the attachments (not a sticker, voice note, like or document)."""
	return any(isinstance(a, dict) and is_photo(a) for a in msg.attachment_info or [])


def is_storable(att: dict) -> bool:
	"""Photos and voice notes/audio are kept in R2 (media_store); stickers, documents, locations... stay metadata."""
	return is_photo(att) or att.get("type") == "audio"


def has_storable_media(attachment_info: list) -> bool:
	return any(isinstance(a, dict) and is_storable(a) for a in attachment_info or [])


def store_photo(data: bytes, folder: str) -> str:
	load_image(data)  # raises NotAnImage
	return default_storage.save(f"{folder}/{uuid4().hex}.{image_format(data)}", ContentFile(data))


def _check_meta_url(url) -> str:
	try:
		parts = urlsplit(url)
		host = parts.hostname or ""
		userinfo = parts.username is not None or parts.password is not None
	except (ValueError, AttributeError, TypeError):
		raise MediaError("invalid media url")
	if parts.scheme != "https" or userinfo or not host.endswith(META_CDN_SUFFIXES):
		raise MediaError("media url is not on a Meta CDN host")
	return url


def _get(url: str, **kwargs) -> requests.Response:
	try:
		resp = requests.get(url, stream=True, timeout=TIMEOUT, allow_redirects=False, **kwargs)
		if 300 <= resp.status_code < 400:
			raise MediaError("unexpected redirect")
		resp.raise_for_status()
		return resp
	except (MediaError, SoftTimeLimitExceeded):
		raise
	except Exception as e:
		raise MediaError(f"download failed: {e}") from e


def _read_capped(chunks, cap: int = MAX_IMAGE_BYTES) -> bytes:
	data = bytearray()
	for chunk in chunks:
		data += chunk
		if len(data) > cap:
			raise MediaError("media too large")
	return bytes(data)


def _download(url: str, cap: int = MAX_IMAGE_BYTES, **kwargs) -> bytes:
	resp = _get(url, **kwargs)
	try:
		return _read_capped(resp.iter_content(chunk_size=64 * 1024), cap)
	except (MediaError, SoftTimeLimitExceeded):
		raise
	except Exception as e:
		raise MediaError(f"download failed: {e}") from e
	finally:
		resp.close()


def _fetch_whatsapp(msg: Message, att: dict, cap: int = MAX_IMAGE_BYTES) -> bytes:
	media_id = (att.get("payload") or {}).get("id")
	cred = tasks.get_whatsapp_credential(msg)
	if not media_id or not cred:
		raise MediaError("missing whatsapp media id or credential")
	headers = {"Authorization": f"Bearer {cred.get_credentials().access_token}"}
	try:
		url = _get(f"{GRAPH_API_URL}/{media_id}", headers=headers).json().get("url")
	except (MediaError, SoftTimeLimitExceeded):
		raise
	except Exception as e:
		raise MediaError(f"media lookup failed: {e}") from e
	return _download(_check_meta_url(url), cap, headers=headers)


def read_stored(path: str, cap: int = MAX_IMAGE_BYTES) -> bytes:
	"""Bytes of a stored object, refusing more than `cap`; MediaError on any failure."""
	try:
		with default_storage.open(path) as f:
			data = f.read(cap + 1)
	except SoftTimeLimitExceeded:
		raise
	except Exception as e:
		raise MediaError(f"cannot read upload: {e}") from e
	if len(data) > cap:
		raise MediaError("media too large")
	return data


def _read_upload(path) -> bytes:
	if not isinstance(path, str) or ".." in path or path.startswith("/") or not path.startswith(UPLOAD_PREFIXES):
		raise MediaError("invalid upload path")
	return read_stored(path)


def fetch_attachment(msg: Message, att: dict, cap: int) -> bytes:
	"""Raw bytes of a Meta channel attachment (photo or audio), downloaded from Meta; MediaError on any failure."""
	channel = msg.conversation.channel
	if channel in ("facebook", "instagram"):
		return _download(_check_meta_url(att.get("url")), cap)
	if channel == "whatsapp":
		return _fetch_whatsapp(msg, att, cap)
	raise MediaError(f"unsupported channel: {channel}")


def fetch_photo(msg: Message, att: dict) -> bytes:
	"""Raw bytes of the photo in `att`: the stored copy when media_store kept one, else from Meta; MediaError on any failure."""
	if att.get("path"):
		return _read_upload(att["path"])
	if msg.conversation.channel in ("widget", "self"):
		raise MediaError("upload has no stored path")
	return fetch_attachment(msg, att, MAX_IMAGE_BYTES)
