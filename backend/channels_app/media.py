"""Fetch customer photos from each channel and store uploaded ones. This is an SSRF boundary: only Meta CDN hosts are fetched."""
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from agents.image_search import MAX_IMAGE_BYTES, image_format, load_image

from . import tasks
from .facebook_client import GRAPH_API_URL
from .models import Message

META_CDN_SUFFIXES = (".fbcdn.net", ".fbsbx.com", ".cdninstagram.com")
UPLOAD_PREFIXES = ("widget_uploads/", "chat_uploads/")
TIMEOUT = 10


class MediaError(Exception):
	pass


def is_photo(att: dict) -> bool:
	return att.get("type") == "image" and not (att.get("payload") or {}).get("sticker_id")


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
	except MediaError:
		raise
	except Exception as e:
		raise MediaError(f"download failed: {e}") from e


def _read_capped(chunks) -> bytes:
	data = bytearray()
	for chunk in chunks:
		data += chunk
		if len(data) > MAX_IMAGE_BYTES:
			raise MediaError("image too large")
	return bytes(data)


def _download(url: str, **kwargs) -> bytes:
	resp = _get(url, **kwargs)
	try:
		return _read_capped(resp.iter_content(chunk_size=64 * 1024))
	except MediaError:
		raise
	except Exception as e:
		raise MediaError(f"download failed: {e}") from e
	finally:
		resp.close()


def _fetch_whatsapp(msg: Message, att: dict) -> bytes:
	media_id = (att.get("payload") or {}).get("id")
	cred = tasks.get_whatsapp_credential(msg)
	if not media_id or not cred:
		raise MediaError("missing whatsapp media id or credential")
	headers = {"Authorization": f"Bearer {cred.get_credentials().access_token}"}
	try:
		url = _get(f"{GRAPH_API_URL}/{media_id}", headers=headers).json().get("url")
	except MediaError:
		raise
	except Exception as e:
		raise MediaError(f"media lookup failed: {e}") from e
	return _download(_check_meta_url(url), headers=headers)


def _read_upload(path) -> bytes:
	if not isinstance(path, str) or ".." in path or path.startswith("/") or not path.startswith(UPLOAD_PREFIXES):
		raise MediaError("invalid upload path")
	try:
		with default_storage.open(path) as f:
			data = f.read(MAX_IMAGE_BYTES + 1)
	except Exception as e:
		raise MediaError(f"cannot read upload: {e}") from e
	if len(data) > MAX_IMAGE_BYTES:
		raise MediaError("image too large")
	return data


def fetch_photo(msg: Message, att: dict) -> bytes:
	"""Raw bytes of the photo in `att`; MediaError on any failure."""
	channel = msg.conversation.channel
	if channel in ("facebook", "instagram"):
		return _download(_check_meta_url(att.get("url")))
	if channel == "whatsapp":
		return _fetch_whatsapp(msg, att)
	if channel in ("widget", "self"):
		return _read_upload(att.get("path"))
	raise MediaError(f"unsupported channel: {channel}")
