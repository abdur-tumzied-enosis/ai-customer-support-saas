"""Cloudflare R2 object storage: the STORAGES setting, plus what default_storage lacks for direct browser uploads
(presigned PUT, HEAD size, a ranged read of the first bytes). Everything else goes through default_storage."""
import base64
from typing import Mapping

from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage

PUT_EXPIRES = 600
GET_EXPIRES = 300
AUDIO_MAX_BYTES = 16 * 1024 * 1024
R2_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
STATICFILES = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}


def r2_endpoint(account_id: str) -> str:
	return f"https://{account_id}.r2.cloudflarestorage.com" if account_id else ""


def r2_storages(env: Mapping[str, str], debug: bool, testing: bool) -> dict:
	"""Value for settings.STORAGES. Tests never construct the S3 backend."""
	if testing:
		return {"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}, "staticfiles": STATICFILES}
	missing = [k for k in R2_VARS if not env.get(k)]
	if missing:
		raise ImproperlyConfigured(f"R2 storage is not configured: set {', '.join(missing)}")
	if debug and env["R2_BUCKET"] == "main":
		raise ImproperlyConfigured("Refusing to use the production bucket 'main' with DEBUG=True")
	return {
		"default": {
			"BACKEND": "storages.backends.s3.S3Storage",
			"OPTIONS": {
				"access_key": env["R2_ACCESS_KEY_ID"],
				"secret_key": env["R2_SECRET_ACCESS_KEY"],
				"bucket_name": env["R2_BUCKET"],
				"endpoint_url": r2_endpoint(env["R2_ACCOUNT_ID"]),
				"region_name": "auto",
				"signature_version": "s3v4",
				"default_acl": None,
				"querystring_auth": True,
				"querystring_expire": GET_EXPIRES,
				"file_overwrite": False,
			},
		},
		"staticfiles": STATICFILES,
	}


def _is_s3() -> bool:
	return hasattr(default_storage, "bucket_name")


def _client():
	return default_storage.connection.meta.client


def _bucket() -> str:
	return default_storage.bucket_name


class ObjectTooLarge(ValueError):
	pass


def presign_put(key: str, content_type: str, sha256_hex: str | None = None, size: int | None = None) -> dict:
	"""A URL the browser PUTs the file to, bound to this key and content type, to exactly `size` bytes when given
	(the browser sends the real Content-Length, so a bigger body is refused), and to the bytes themselves when
	sha256_hex is given (R2 rejects a body whose checksum differs). The client must send `headers` exactly."""
	params = {"Bucket": _bucket(), "Key": key, "ContentType": content_type}
	if size is not None:
		params["ContentLength"] = size
	headers = {"Content-Type": content_type}
	if sha256_hex:
		params["ChecksumSHA256"] = headers["x-amz-checksum-sha256"] = base64.b64encode(bytes.fromhex(sha256_hex)).decode()
	url = _client().generate_presigned_url("put_object", Params=params, ExpiresIn=PUT_EXPIRES)
	return {"url": url, "headers": headers}


def presign_get(key: str) -> str:
	return default_storage.url(key, expire=GET_EXPIRES) if _is_s3() else default_storage.url(key)


def object_size(key: str) -> int | None:
	"""Size in bytes, or None when the object does not exist."""
	try:
		return default_storage.size(key)
	except Exception:
		return None


def read_head(key: str, n: int = 32) -> bytes:
	"""The first n bytes, without downloading the rest."""
	if _is_s3():
		return _client().get_object(Bucket=_bucket(), Key=key, Range=f"bytes=0-{n - 1}")["Body"].read()
	with default_storage.open(key, "rb") as f:
		return f.read(n)


def read_capped(key: str, cap: int) -> bytes:
	"""The object's bytes, raising ObjectTooLarge past `cap` without downloading more than cap + 1 bytes
	(default_storage.open() on S3 would pull the whole object into memory first)."""
	if _is_s3():
		obj = _client().get_object(Bucket=_bucket(), Key=key, Range=f"bytes=0-{cap}")
		total = int(str(obj.get("ContentRange") or "/0").rsplit("/", 1)[1])
		if total > cap:
			raise ObjectTooLarge(f"{key} is {total} bytes, over {cap}")
		return obj["Body"].read()
	with default_storage.open(key, "rb") as f:
		data = f.read(cap + 1)
	if len(data) > cap:
		raise ObjectTooLarge(f"{key} is over {cap} bytes")
	return data


def delete_prefix(prefix: str) -> None:
	"""Delete every object whose key starts with `prefix` (a folder, ending in /)."""
	if _is_s3():
		pages = _client().get_paginator("list_objects_v2").paginate(Bucket=_bucket(), Prefix=prefix)
		for page in pages:
			keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
			if keys:  # at most 1000 per page, the delete_objects limit
				_client().delete_objects(Bucket=_bucket(), Delete={"Objects": keys, "Quiet": True})
		return
	try:
		dirs, files = default_storage.listdir(prefix)
	except FileNotFoundError:
		return
	delete_keys(*(prefix + f for f in files))
	for d in dirs:
		delete_prefix(f"{prefix}{d}/")


def delete_keys(*keys: str) -> None:
	"""Delete each key; empty or missing keys are ignored (S3 ignores missing ones itself)."""
	for key in keys:
		if key:
			try:
				default_storage.delete(key)
			except (FileNotFoundError, KeyError):
				pass


_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"))


def sniff_image(head: bytes) -> str | None:
	"""'jpg' | 'png' | 'webp' from the file's magic bytes, else None. A cheap pre-check; workers still decode."""
	for magic, ext in _MAGIC:
		if head.startswith(magic):
			return ext
	if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
		return "webp"
	return None


def thumb_key(name: str) -> str:
	stem, _, _ext = name.rpartition(".")
	return f"{stem}_thumb.webp"
