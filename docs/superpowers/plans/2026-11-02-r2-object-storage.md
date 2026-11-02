# R2 Object Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every stored file lives in a private Cloudflare R2 bucket. Browsers upload and download directly via presigned URLs, and workers resize photos and store inbound channel media.

**Architecture:** `django-storages` S3 backend pointed at R2 becomes `default_storage` everywhere (tests use `InMemoryStorage`). A small `core/storage.py` adds what `default_storage` lacks: presigned PUT, HEAD size, and a ranged read of the first bytes. Browser uploads are presign → PUT → confirm. Workers do the full validation, resizing (`display` + `thumb` WebP) and embedding. A single `store_message_media` task stores chat photos and audio before the reply runs.

**Tech Stack:** Django 5.0 / DRF, Celery, django-storages[s3] + boto3, Pillow, Next.js (TypeScript), vanilla JS widget.

**Spec:** `docs/superpowers/specs/2026-11-02-r2-object-storage-design.md`

## Global Constraints

- Env vars: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`. Bucket `local` in dev, `main` in prod.
- Endpoint `https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`, region `auto`, SigV4.
- Startup fails if any R2 var is missing (not under tests). Refuse `R2_BUCKET=main` when `DEBUG=True`.
- Files are private: `default_acl=None`, `querystring_auth=True`, `file_overwrite=False`.
- Presigned PUT expiry 600 s. Presigned GET expiry 300 s.
- Size caps: product photo 10 MB (`MAX_PRODUCT_IMAGE_BYTES`), chat photo 5 MB (`MAX_IMAGE_BYTES`), document 10 MB (`MAX_DOCUMENT_FILE_SIZE`), audio 16 MB.
- Allowed upload image types: `image/jpeg`, `image/png`, `image/webp`.
- Variants: `display` long edge ≤ 1600 px, WebP q 80, replaces the original. `thumb` long edge ≤ 320 px WebP at `<stem>_thumb.webp`. Browser pre-downscale long edge ≤ 2048 px. No upscaling.
- No file bytes through a web worker, except the ≤ 32-byte ranged read at chat-photo confirm.
- `MEDIA_ROOT` is removed everywhere. No local-disk fallback in the app.
- Abandoned DB-tracked uploads (`status="uploading"`) are cleaned after 1 hour.
- Tests: `docker compose run --rm django python manage.py test <dotted.module> -v 2` from repo root. Frontend: `cd frontend && npx tsc --noEmit && npm run lint`.
- Match the house style: tabs in the newer modules, `{"success": ..., "data"/"error": ...}` responses, `ponytail:` comments for known ceilings.

## Review Focus

1. **Key from another tenant or visitor at confirm.** A widget visitor sends a `key` presigned for a different widget or visitor, or a `../` path. Expect 400 `invalid_upload`, no message created. → Task 6 test `test_confirm_rejects_foreign_key`.
2. **Confirm without a PUT, or a PUT larger than declared.** Expect the image or document to go to `failed`, its key to be deleted, and the error code `upload_missing` / `upload_too_large`. → Task 3 `test_confirm_missing_object_marks_failed`, Task 4 `test_confirm_oversize_marks_failed`.
3. **Inbound media download fails** (Meta 404, timeout, oversize audio). Expect the reply to still be scheduled and the attachment left without `path`. → Task 5 `test_media_failure_still_schedules_reply`.
4. **Confirm called twice** (double click or retry). Expect idempotence: no second embed or ingestion job, same 200. → Task 3 `test_confirm_twice_queues_once`, Task 4 `test_confirm_twice_creates_one_job`.
5. **Photo already small, or a portrait JPEG with EXIF rotation.** Expect no upscaling and the orientation applied. → Task 2 `test_small_image_not_upscaled`, `test_exif_orientation_applied`.

---

### Task 1: R2 storage backend, helpers and setup

**Files:**
- Create: `backend/core/storage.py`, `backend/core/tests/__init__.py`, `backend/core/tests/test_storage.py`, `docs/r2-storage.md`
- Modify: `backend/requirements.txt`, `backend/core/settings.py:105-107`, `.env.example`, `docker-compose.coolify.yml` (drop `MEDIA_ROOT`, the `media:/app/media` mount and the `media` volume)
- Modify tests: remove every `override_settings(MEDIA_ROOT=...)` / `tempfile` media dir (`channels_app/tests/test_widget.py:257`, `test_test_chat_photo.py:21`, `knowledge/tests/test_suggestions.py:17`, `test_product_api.py:35`, `test_products.py:18`). `InMemoryStorage` replaces them.

**Interfaces:**
- Produces in `core/storage.py`:
  - `r2_storages(env: Mapping[str, str], debug: bool, testing: bool) -> dict`: value for `settings.STORAGES`. Raises `ImproperlyConfigured` on a missing var, or on `main` with `debug`.
  - `PUT_EXPIRES = 600`, `GET_EXPIRES = 300`, `AUDIO_MAX_BYTES = 16 * 1024 * 1024`
  - `presign_put(key: str, content_type: str, sha256_hex: str | None = None) -> dict`: returns `{"url": str, "headers": {"Content-Type": ..., ["x-amz-checksum-sha256": <base64>]}}`
  - `presign_get(key: str) -> str`: `default_storage.url(key, expire=GET_EXPIRES)`
  - `object_size(key: str) -> int | None`: `None` when missing
  - `read_head(key: str, n: int = 32) -> bytes`: ranged GET on S3, `open().read(n)` on other backends
  - `delete_keys(*keys: str) -> None`: ignores missing/empty
  - `sniff_image(head: bytes) -> str | None`: `"jpg" | "png" | "webp"` from magic bytes
  - `thumb_key(name: str) -> str`: `"a/b/x.webp"` → `"a/b/x_thumb.webp"`
- `settings.TESTING: bool`, `settings.R2_ENDPOINT_URL: str` (empty under tests)

- [ ] **Step 1: Write failing tests** in `core/tests/test_storage.py`:

```python
ENV = {"R2_ACCOUNT_ID": "acc", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": "local"}

def test_testing_uses_in_memory(self):
	assert r2_storages({}, debug=True, testing=True)["default"]["BACKEND"] == "django.core.files.storage.InMemoryStorage"

def test_missing_var_raises(self):
	with self.assertRaises(ImproperlyConfigured):
		r2_storages({**ENV, "R2_BUCKET": ""}, debug=True, testing=False)

def test_main_bucket_refused_in_debug(self):
	with self.assertRaises(ImproperlyConfigured):
		r2_storages({**ENV, "R2_BUCKET": "main"}, debug=True, testing=False)

def test_r2_options(self):
	opts = r2_storages(ENV, debug=False, testing=False)["default"]["OPTIONS"]
	assert opts["bucket_name"] == "local"
	assert opts["endpoint_url"] == "https://acc.r2.cloudflarestorage.com"
	assert opts["region_name"] == "auto" and opts["default_acl"] is None
	assert opts["querystring_auth"] is True and opts["file_overwrite"] is False

def test_sniff_image(self):
	assert sniff_image(b"\xff\xd8\xff\xe0" + b"0" * 28) == "jpg"
	assert sniff_image(b"\x89PNG\r\n\x1a\n" + b"0" * 24) == "png"
	assert sniff_image(b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 20) == "webp"
	assert sniff_image(b"%PDF-1.7") is None

def test_thumb_key(self):
	assert thumb_key("kb/1/products/abc.webp") == "kb/1/products/abc_thumb.webp"

def test_presign_put_signs_checksum(self):
	# patch core.storage._client() -> MagicMock; generate_presigned_url returns "https://signed"
	r = presign_put("tmp/x.jpg", "image/jpeg", sha256_hex="00" * 32)
	params = client.generate_presigned_url.call_args.kwargs["Params"]
	assert params["Key"] == "tmp/x.jpg" and params["ContentType"] == "image/jpeg"
	assert params["ChecksumSHA256"] == base64.b64encode(bytes(32)).decode()
	assert client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 600
	assert r["headers"]["x-amz-checksum-sha256"] == params["ChecksumSHA256"]

def test_object_size_and_read_head_in_memory(self):
	default_storage.save("t/a.bin", ContentFile(b"abcdef"))
	assert object_size("t/a.bin") == 6 and object_size("t/missing") is None
	assert read_head("t/a.bin", 3) == b"abc"
```

- [ ] **Step 2: Run, expect ImportError**

Run: `docker compose run --rm django python manage.py test core.tests.test_storage -v 2`
Expected: FAIL `ModuleNotFoundError: No module named 'core.storage'` (or ImportError).

- [ ] **Step 3: Implement `core/storage.py`, the settings and requirements**
  - `requirements.txt`: add `django-storages[s3]==1.14.4` and `boto3==1.35.99`.
  - `r2_storages` returns `{"default": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": {...}}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}`. Options: `access_key`, `secret_key`, `bucket_name`, `endpoint_url`, `region_name="auto"`, `signature_version="s3v4"`, `default_acl=None`, `querystring_auth=True`, `querystring_expire=GET_EXPIRES`, `file_overwrite=False`, plus `client_config=botocore.config.Config(request_checksum_calculation="when_required", response_checksum_validation="when_required")` (R2's recommended boto config). Under `testing`, `default` is `InMemoryStorage`.
  - `_client()` returns `default_storage.connection.meta.client`. `presign_put` calls `generate_presigned_url("put_object", Params={"Bucket", "Key", "ContentType", ["ChecksumSHA256"]}, ExpiresIn=PUT_EXPIRES)`.
  - `read_head` on S3 uses `_client().get_object(Bucket, Key, Range=f"bytes=0-{n-1}")["Body"].read()`.
  - settings: replace the `MEDIA_ROOT` block with `TESTING = "test" in sys.argv`, `STORAGES = r2_storages(os.environ, DEBUG, TESTING)` and `R2_ENDPOINT_URL`.
- [ ] **Step 4: Run, expect PASS.** Then run the whole suite: `docker compose run --rm django python manage.py test -v 1`. Expected: everything that passed on `main` still passes, with no `MEDIA_ROOT` references left (`grep -rn MEDIA_ROOT backend` is empty).
- [ ] **Step 5: Write `docs/r2-storage.md` and `.env.example`.** The doc covers: create buckets `local` and `main`, one API token per bucket (Object Read & Write, that bucket only), the env vars, the lifecycle rule "delete objects under prefix `tmp/` after 1 day", and this CORS rule on both buckets:

```json
[{"AllowedOrigins": ["http://localhost:3000", "http://localhost:8000", "https://<frontend-domain>", "https://<backend-domain>"],
  "AllowedMethods": ["PUT", "GET"], "AllowedHeaders": ["Content-Type", "x-amz-checksum-sha256"], "MaxAgeSeconds": 3600}]
```

- [ ] **Step 6: Smoke test against the real `local` bucket.** With a real `.env`, run:
  `docker compose run --rm django python manage.py shell -c "from core.storage import presign_put; import hashlib,requests; b=b'hello'; h=hashlib.sha256(b).hexdigest(); p=presign_put('tmp/smoke.txt','text/plain',h); print(requests.put(p['url'],data=b,headers=p['headers']).status_code, requests.put(p['url'],data=b'tampered',headers=p['headers']).status_code)"`
  Expected: `200 400` (R2 enforces the signed checksum). **If the second code is 200, record it in the PR.** Task 4 then uses the spec's fallback: the ingestion worker recomputes the hash.
- [ ] **Step 7: Commit** `feat(storage): store uploads in Cloudflare R2 via django-storages`

---

### Task 2: Photo variants

**Files:**
- Modify: `backend/agents/image_search.py` (next to `load_image`)
- Test: `backend/agents/tests/test_image_search.py`

**Interfaces:**
- Produces: `make_variants(data: bytes, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, bytes]`, returning `(display_webp, thumb_webp)`. Raises `NotAnImage` like `load_image`. Constants `DISPLAY_EDGE = 1600`, `THUMB_EDGE = 320`, `WEBP_QUALITY = 80`.

- [ ] **Step 1: Failing tests**

```python
def test_variants_bounded_webp(self):
	display, thumb = make_variants(png_bytes(4000, 3000))
	d, t = Image.open(BytesIO(display)), Image.open(BytesIO(thumb))
	assert d.format == "WEBP" and max(d.size) == 1600
	assert t.format == "WEBP" and max(t.size) == 320

def test_small_image_not_upscaled(self):
	d = Image.open(BytesIO(make_variants(png_bytes(200, 100))[0]))
	assert d.size == (200, 100)

def test_exif_orientation_applied(self):
	# 300x100 JPEG with EXIF Orientation=6 (rotate 90)
	d = Image.open(BytesIO(make_variants(jpeg_with_orientation(300, 100, 6))[0]))
	assert d.size == (100, 300) and not d.getexif()

def test_variants_reject_non_image(self):
	with self.assertRaises(NotAnImage):
		make_variants(b"%PDF-1.7 not an image")
```

- [ ] **Step 2: Run `agents.tests.test_image_search`, expect FAIL** (`make_variants` not defined).
- [ ] **Step 3: Implement.** `load_image` → `ImageOps.exif_transpose` → convert to RGB (keep alpha as RGBA) → `copy().thumbnail((EDGE, EDGE))` for each → `save(buf, "WEBP", quality=WEBP_QUALITY)` with no `exif=`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(image-search): make display and thumb webp variants`

---

### Task 3: Catalog photos (presign, confirm, resize, redirect, cleanup)

**Files:**
- Modify: `backend/knowledge/products.py` (replace `_clean_files` / `_store` and the multipart paths), `backend/knowledge/models.py` (`ProductImage.STATUS_CHOICES` gets `("uploading", "Uploading")`; `_delete_file` also deletes `thumb_key`), `backend/knowledge/tasks.py:757` (`embed_product_image`), `backend/knowledge/urls.py`, `backend/core/settings.py` (beat entry)
- Create: migration for the choice change
- Test: `backend/knowledge/tests/test_product_api.py`, `backend/knowledge/tests/test_products.py` (rewrite the multipart cases)

**Interfaces:**
- Consumes: Task 1 `presign_put`, `presign_get`, `object_size`, `delete_keys`, `thumb_key`; Task 2 `make_variants`.
- Produces (HTTP, JSON bodies):
  - `POST /api/kb/products/` `{project_id, kb_id, name, description, files: [{name, size, content_type}]}` → 201 `{"data": {"product": <product_dict>, "uploads": [{"image_id", "url", "headers"}]}}`. Creates the product and one `uploading` `ProductImage` per file, with key `product_image_path(kb, id, ext)` placed under the `tmp/` prefix (`"tmp/" + product_image_path(...)`). Errors unchanged: `no_images`, `too_many_images`, `image_too_large`, plus `invalid_image` for a content type outside the allowed three.
  - `POST /api/kb/products/<id>/images/` `{files: [...]}` → 201, same `uploads` shape.
  - `POST /api/kb/products/images/confirm/` `{image_ids: [...]}` → 200 `{"data": [<image_dict>...]}`. Per image (admin gate as delete), only rows in `uploading` change. Missing object → `failed`/`upload_missing`. Size > cap → `failed`/`upload_too_large` plus key deleted. Otherwise `pending` and `embed_product_image.delay` on commit.
  - `GET /api/kb/products/images/<id>/file/?size=thumb|display` → 302 to `presign_get(key)`; `thumb` falls back to the display key when the image isn't `ready`.
  - `_image_dict` adds `"thumb_url": url + "?size=thumb"`.
  - `knowledge.tasks.cleanup_abandoned_uploads()`: beat every 900 s. Deletes `uploading` `ProductImage` and `Document` rows older than 1 h with their keys, then products with zero images.
  - `embed_product_image`: read the `tmp/` key → `make_variants(data, MAX_PRODUCT_IMAGE_BYTES)` → save display at `product_image_path(kb, id, "webp")` and the thumb at `thumb_key(...)` → delete the tmp key → update `file.name` → embed `display`. On `NotAnImage`, delete the tmp key and set `failed`.

- [ ] **Step 1: Failing tests** (patch `knowledge.products.presign_put` to return `{"url": "https://r2/put", "headers": {}}`, and `object_size` as needed):

```python
def test_create_returns_uploads_and_uploading_rows(self):
	r = self.post_json("/api/kb/products/", {..., "files": [{"name": "a.jpg", "size": 1000, "content_type": "image/jpeg"}]})
	assert r.status_code == 201
	img = ProductImage.objects.get()
	assert img.status == "uploading" and img.file.name.startswith("tmp/")
	assert r.json()["data"]["uploads"][0]["image_id"] == str(img.id)

def test_create_rejects_oversize_declared(self):   # size 10 MB + 1 -> 400 image_too_large, no Product row
def test_create_rejects_bad_content_type(self):    # "application/pdf" -> 400 invalid_image
def test_confirm_queues_embed(self):               # object_size -> 1000; status pending; embed .delay called once
def test_confirm_missing_object_marks_failed(self):  # object_size -> None; status failed, error "upload_missing"
def test_confirm_twice_queues_once(self):          # second confirm: 200, .delay still called once
def test_file_view_redirects_to_presigned(self):   # patch presign_get -> "https://r2/get"; 302, Location == that
def test_thumb_falls_back_when_not_ready(self):    # pending image, ?size=thumb -> presign_get called with display key
def test_cleanup_removes_abandoned_and_empty_products(self):
	# uploading image created 2h ago (update created_at) -> row gone, key deleted, product gone
	# uploading image created 10 min ago -> kept
def test_embed_writes_variants_and_drops_tmp(self):
	# save a real PNG at the tmp key; run embed_product_image (patch embed_image/upsert_image)
	# -> file.name ends ".webp", not under tmp/; default_storage.exists(thumb_key(name)); tmp key gone
def test_delete_image_removes_thumb(self):
```

- [ ] **Step 2: Run `knowledge.tests.test_product_api knowledge.tests.test_products`, expect FAIL.**
- [ ] **Step 3: Implement** per the Interfaces. `FileResponse` and `ContentFile` imports go away from `products.py`; the multipart parser is no longer needed there.
- [ ] **Step 4: Run, expect PASS.** Also run `knowledge.tests` and `accounts.tests` (account deletion deletes images).
- [ ] **Step 5: Commit** `feat(products): upload catalog photos straight to R2 and resize them`

---

### Task 4: Knowledge documents (presign with checksum, confirm)

**Files:**
- Modify: `backend/knowledge/views.py:190-300` (`DocumentUploadView`), `backend/knowledge/urls.py`
- Test: `backend/knowledge/tests/test_document_upload.py` (rewrite)

**Interfaces:**
- Consumes: Task 1 `presign_put(key, content_type, sha256_hex)`, `object_size`, `delete_keys`; Task 3 `cleanup_abandoned_uploads` already covers `Document.status == "uploading"`.
- Produces:
  - `POST /api/kb/documents/upload/` JSON `{project_id, kb_id, filename, size, content_type, sha256}`. `sha256` is 64 lowercase hex chars, otherwise 400 `invalid_checksum`. Existing checks in the same order and with the same codes: `missing_project`, `access_denied`, `kb_not_found`, `invalid_file_type`, `file_too_large`, `duplicate_file` (409, now from the client hash). Creates `Document(status="uploading", checksum=sha256, file=document_storage_path(kb, f"{id}_{filename}"))` → 201 `{"data": {"document_id", "url", "headers"}}`.
  - `POST /api/kb/documents/<uuid:document_id>/confirm/` → 200 `{"data": {"document_id", "job_id"}}`. Missing object → `failed` + 400 `upload_missing`. Size > `MAX_DOCUMENT_FILE_SIZE` → `failed` + key deleted + 400 `upload_too_large`. Otherwise `processing`, plus the existing `IngestionJob` create and dispatch block, moved unchanged into `_start_ingestion(document) -> IngestionJob`. A non-`uploading` document returns its latest job (idempotent).
  - If the Task 1 smoke showed that R2 does **not** enforce checksums: `run_ingestion_job` recomputes the SHA-256 of the read bytes and fails the document with `checksum_mismatch` when it differs.

- [ ] **Step 1: Failing tests**

```python
def test_presign_creates_uploading_document(self):   # 201, Document.status == "uploading", presign_put called with sha256_hex
def test_presign_duplicate_409(self):                # existing completed doc with same checksum -> 409 duplicate_file, no new row
def test_presign_bad_checksum_400(self):             # sha256 "xyz" -> 400 invalid_checksum
def test_presign_too_large_400(self):                # size MAX_DOCUMENT_FILE_SIZE + 1 -> file_too_large
def test_confirm_starts_ingestion(self):             # object_size -> 100; status processing; one IngestionJob; task dispatched
def test_confirm_oversize_marks_failed(self):        # object_size -> MAX + 1; failed; delete_keys called; 400 upload_too_large
def test_confirm_twice_creates_one_job(self):
def test_confirm_other_project_404(self):
```

- [ ] **Step 2: Run `knowledge.tests.test_document_upload`, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.** Also `knowledge.tests.test_ingestion_job`.
- [ ] **Step 5: Commit** `feat(knowledge): upload documents straight to R2 with a signed checksum`

---

### Task 5: Store chat media (photos + audio) before the reply

**Files:**
- Create: `backend/channels_app/media_store.py`
- Modify: `backend/channels_app/media.py` (`fetch_photo`, `_read_upload`, `UPLOAD_PREFIXES`; add a fetch of raw attachment bytes with a cap), `backend/channels_app/service/whatsapp_messaging.py:47-73`, `backend/channels_app/service/facebook_messing.py:146-198`, `backend/core/settings.py` (`CELERY_TASK_ROUTES`: the task goes to the `replies` queue)
- Test: `backend/channels_app/tests/test_media_store.py`, plus updates in `test_media.py`, `test_whatsapp.py`, `test_facebook_conversation.py`, `test_instagram.py`

**Interfaces:**
- Consumes: Task 1 `delete_keys`, `AUDIO_MAX_BYTES`; Task 2 `make_variants`; existing `media._get`, `_check_meta_url`, `_fetch_whatsapp`, `reply.schedule_reply`.
- Produces:
  - `channels_app.media_store.store_message_media(message_id: str, reply: bool) -> None` (Celery task). For each attachment where `media.is_photo(att)` or `att["type"] == "audio"` and there is no `path` yet, or the `path` starts with `tmp/`: get the bytes (a `tmp/` path is read from storage, otherwise fetched from Meta with a cap of 5 MB for images and `AUDIO_MAX_BYTES` for audio). Then:
    - image → `make_variants` → save `chat_media/<project.slug>/<uuid>.webp` and its thumb, delete the `tmp/` key, set `att["path"]`
    - audio → save the raw bytes at `chat_media/<project.slug>/<uuid>.<ext>`. The ext comes from the mime type: ogg, mp4/m4a, mpeg/mp3, amr; anything else is `bin`.

    Each attachment's failure is logged and its `path` left as is. Saves `attachment_info`. In `finally`, if `reply`, calls `schedule_reply(msg)`.
  - `media.has_storable_media(attachment_info: list) -> bool`
  - `media.UPLOAD_PREFIXES = ("chat_media/",)`. `fetch_photo` reads `att["path"]` via `_read_upload` whenever it's present, for any channel, and otherwise falls back to the Meta fetch as today.
  - Meta handlers: if `has_storable_media(attachments)`, then `transaction.on_commit(lambda: store_message_media.delay(str(msg.id), wants_reply))` replaces `schedule_reply`. Otherwise unchanged.

- [ ] **Step 1: Failing tests** (patch the Meta download to return fixture bytes):

```python
def test_photo_stored_as_variants_and_reply_scheduled(self):
	# whatsapp msg with image attachment; run store_message_media(id, True)
	# -> att["path"] startswith "chat_media/<slug>/" and endswith ".webp"; thumb exists; schedule_reply called once

def test_audio_stored_raw(self):
	# att {"type": "audio", "payload": {"id": "m1", "mime_type": "audio/ogg; codecs=opus"}} -> path endswith ".ogg", bytes equal fixture

def test_oversize_audio_skipped(self):        # download raises MediaError("too large") -> no path, no exception
def test_media_failure_still_schedules_reply(self):  # download raises -> schedule_reply still called when reply=True
def test_no_reply_flag_does_not_schedule(self):
def test_tmp_upload_moved_to_chat_media(self):  # att {"type": "image", "path": "tmp/widget/..."} saved in storage -> new path, tmp gone
def test_sticker_ignored(self):               # image with sticker_id -> untouched
def test_whatsapp_audio_message_queues_store(self):  # webhook with type audio -> store_message_media.delay(id, False), no schedule_reply
def test_fetch_photo_prefers_stored_path(self):      # att with path -> reads storage, no HTTP call
```

- [ ] **Step 2: Run `channels_app.tests.test_media_store`, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `channels_app.tests`, expect PASS.** `recover_stuck_replies` still covers a crashed store task, because the message stays `pending`.
- [ ] **Step 5: Commit** `feat(channels): keep inbound photos and voice notes in R2`

---

### Task 6: Widget and test-chat photo presign/confirm

**Files:**
- Modify: `backend/channels_app/widget.py` (`WidgetPhotoView` becomes JSON confirm; new `WidgetPhotoPresignView`; CSP at `:206-207` adds `settings.R2_ENDPOINT_URL` to `connect-src`), `backend/channels_app/urls_widget.py`, `backend/channels_app/api.py:534-555` (`ConversationPhotoView` becomes JSON confirm; new presign view; `read_photo_upload` removed), `backend/channels_app/urls_api.py`, `backend/channels_app/media.py` (`store_photo` removed)
- Test: `backend/channels_app/tests/test_widget.py`, `backend/channels_app/tests/test_test_chat_photo.py` (rewrite the photo cases)

**Interfaces:**
- Consumes: Task 1 `presign_put`, `object_size`, `read_head`, `sniff_image`; Task 5 `store_message_media`.
- Produces:
  - `POST /api/widget/messages/photo/presign` `{content_type, size}` (same auth and throttles as messages, plus `photo_disabled` gate) → 200 `{"data": {"key", "url", "headers"}}`. The key is `tmp/widget/<widget.id>/<sha256(visitor)[:16]>/<uuid>.<ext>`. Bad type or `size > MAX_IMAGE_BYTES` → 400 `invalid_image` with today's message "Send a JPEG, PNG or WebP image up to 5 MB".
  - `POST /api/widget/messages/photo` `{key, text, client_id}`. A key outside that exact visitor prefix, or containing `..` → 400 `invalid_upload`. Then `object_size` missing or over the cap, or `sniff_image(read_head(key))` is `None` → 400 `invalid_image` (same message). Then the existing client_id dedupe and message creation with `attachment_info=[{"type": "image", "path": key}]`, then `store_message_media.delay(id, True)` on commit instead of `schedule_reply`.
  - Test chat: `POST /api/channels/conversations/<id>/photo/presign` and `POST .../photo` `{key, text}`, same rules, key prefix `tmp/chat/<project.slug>/<conversation.id>/`.

- [ ] **Step 1: Failing tests**

```python
def test_presign_returns_visitor_scoped_key(self):  # key startswith f"tmp/widget/{widget.id}/"
def test_presign_rejects_large(self):               # size 5 MB + 1 -> 400 invalid_image
def test_confirm_creates_message_and_queues_store(self):  # save PNG at key; 201; store_message_media.delay called with (id, True)
def test_confirm_rejects_foreign_key(self):         # key of another visitor / "tmp/widget/<id>/../x" -> 400 invalid_upload, no Message
def test_confirm_rejects_non_image(self):           # save b"%PDF..." at key -> 400 invalid_image
def test_confirm_client_id_dedupe(self):            # same client_id twice -> one Message
def test_csp_allows_r2(self):                       # override_settings(R2_ENDPOINT_URL="https://acc.r2.cloudflarestorage.com") -> in connect-src
# test_test_chat_photo.py: same five cases for the test-chat endpoints, plus the existing 404/400 gates kept
```

- [ ] **Step 2: Run `channels_app.tests.test_widget channels_app.tests.test_test_chat_photo`, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `channels_app.tests`, expect PASS.**
- [ ] **Step 5: Commit** `feat(channels): widget and test-chat photos upload straight to R2`

---

### Task 7: Dashboard frontend

**Files:**
- Create: `frontend/lib/upload.ts`
- Modify: `frontend/lib/api.ts` (`documentApi.upload` at `:187`, `productApi.create` / `addImages` at `:1016-1034`, conversation photo at `:735`, `Product` image type gets `thumb_url`), `frontend/components/knowledge/ProductCatalog.tsx:220` (`src` uses `thumb_url`), the document upload caller(s) and `frontend/components/chat/MessageComposer.tsx` only where their call signatures change

**Interfaces:**
- Consumes: the Task 3, 4 and 6 HTTP shapes.
- Produces in `lib/upload.ts`:
  - `downscaleImage(file: File, maxEdge = 2048): Promise<Blob>`: `createImageBitmap` + canvas → `image/jpeg` q 0.9. Returns the original file when it's already ≤ maxEdge, or when decoding fails (the server still validates).
  - `sha256Hex(file: Blob): Promise<string>` via `crypto.subtle.digest`
  - `putToR2(upload: { url: string; headers: Record<string, string> }, body: Blob): Promise<void>`. Throws `ApiError("upload_failed", ...)` on a non-2xx response.
- `productApi.create(projectId, kbId, name, description, files)` / `addImages(id, files)` keep their signatures and return `Product`: downscale → presign → parallel `putToR2` → confirm → `Product` (the refetched product from the confirm response or a list reload). `documentApi.upload(file, projectId, kbId)` keeps its signature: hash → presign → PUT → confirm.

- [ ] **Step 1: Implement `lib/upload.ts` and rewire `api.ts`.** The size the presign call declares is the size of the blob actually sent (after downscaling).
- [ ] **Step 2: Type-check and lint.** Run: `cd frontend && npx tsc --noEmit && npm run lint`. Expected: no errors.
- [ ] **Step 3: Manual check against the `local` bucket** (`docker compose up`):
  1. Create a product with a ~8 MB phone photo. In the network tab, the PUT goes to `*.r2.cloudflarestorage.com` and the Django requests are JSON only. The status goes `pending` → `ready`, and the grid image loads through a 302.
  2. Upload a PDF, then upload it again. The second upload shows "already uploaded" before any PUT.
  3. Send a test-chat photo. The bot replies with a catalog match.
- [ ] **Step 4: Commit** `feat(frontend): upload photos and documents straight to R2`

---

### Task 8: Widget client

**Files:**
- Modify: `backend/channels_app/widget_assets/app.js:59-170` (`photoForm` and the photo branch of the send queue)

**Interfaces:**
- Consumes: the Task 6 widget endpoints.
- Produces: the photo send becomes downscale (canvas, long edge ≤ 2048, JPEG) → `POST /api/widget/messages/photo/presign` → `fetch(url, {method: "PUT", headers, body})` → `POST /api/widget/messages/photo` `{key, text, client_id}`. Retries reuse the same `client_id`; a failed PUT surfaces the same error bubble as a failed send today.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Manual check.** Embed the widget on a local test page and send a photo. The PUT goes to R2 and isn't blocked by CSP (check the console), and the bot replies. Send a `.txt` renamed `.jpg`: the visitor sees the "Send a JPEG, PNG or WebP image up to 5 MB" error.
- [ ] **Step 3: Run `channels_app.tests.test_widget`** (asset serving still passes).
- [ ] **Step 4: Commit** `feat(widget): send photos straight to R2`
