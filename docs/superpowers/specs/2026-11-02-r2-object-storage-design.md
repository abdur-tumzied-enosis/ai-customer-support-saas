# R2 Object Storage — Design

Date: 2026-11-02
Status: approved

## Why

Uploads (catalog photos, knowledge documents, widget/test-chat photos) currently stream through the Django web
process and land on a local disk / shared Docker volume (`MEDIA_ROOT`). We want file bytes off the main backend
server entirely: browsers upload straight to object storage, Django only signs and records. Cloudflare R2 is the
object store, for images and audio.

## Goals

- All stored files (images, documents, audio) live in Cloudflare R2, **in every environment including local dev**.
- Bucket per environment: `local` for dev, `main` for prod. Chosen by env var.
- Files are **private**. Nothing is publicly readable; access is via short-lived presigned URLs handed out by Django
  after its normal permission checks.
- Browser uploads go **directly to R2** via presigned PUT URLs. Django handles only small JSON calls.
- Downloads go **directly from R2** via presigned GET URLs. Django never streams file bytes to browsers.
- Inbound channel media (Messenger/Instagram/WhatsApp images and audio) is downloaded by Celery workers and kept in R2.
  Audio is **store only**.

## Non-goals

- No audio transcription, playback UI, or any other audio feature beyond storing the file.
- No public bucket, custom CDN domain, or on-the-fly image transformation service (resizing is done once in the
  worker, see 4b).
- No migration/backfill of existing local files (no prod data worth moving).
- No local-disk fallback: `MEDIA_ROOT` and the media volumes are removed, not kept alongside.

## Anti-goals (failure even if it "works")

- Any path where an upload or download streams full file bytes through a gunicorn/uvicorn web worker.
- A presigned URL that lets a client write outside the exact key Django chose, or read files of another tenant.
- Local dev or tests silently writing to the `main` bucket.

## Current state (what the builder will touch)

All app code already goes through Django `default_storage`, which keeps the swap small:

| Area | File | Today |
|---|---|---|
| Settings | `backend/core/settings.py:107` | `MEDIA_ROOT` env, default filesystem storage |
| Catalog photo upload | `backend/knowledge/products.py:66` (`request.FILES.getlist("images")`), save at `:94` | multipart through Django |
| Catalog photo serve | `backend/knowledge/products.py:238` `ProductImageFileView` | `FileResponse` streams bytes |
| Catalog photo URL in API | `backend/knowledge/products.py:27` | `/api/kb/products/images/{id}/file/` |
| Knowledge doc upload | `backend/knowledge/views.py:218` | multipart through Django |
| Doc text extraction | `backend/knowledge/tasks.py:52` | `default_storage.open()` (works unchanged on R2) |
| Widget photo | `backend/channels_app/widget.py:393` → `store_photo` | multipart through Django |
| Test-chat photo | `backend/channels_app/api.py:548` → `store_photo` | multipart through Django |
| Photo storage helper | `backend/channels_app/media.py:35` `store_photo`, `:100` `_read_upload` | `default_storage` |
| Inbound Meta photos | `backend/channels_app/media.py` `fetch_photo` | fetched from Meta on demand, only when image search is on; **not stored** |
| Inbound audio etc. | `whatsapp_messaging.py:47`, `facebook_messing.py:146` | metadata in `Message.attachment_info`; **bytes never fetched** |
| Compose | `docker-compose.coolify.yml` only (`MEDIA_ROOT: /app/media`, `media` volume); dev compose relies on the `./backend:/app` bind-mount | local volume |
| Frontend | `frontend/lib/api.ts:1033` (catalog `postForm`), `components/knowledge/ProductCatalog.tsx:220` (`<img src=API_BASE_URL+img.url>`), `components/chat/MessageComposer.tsx` | multipart; cookie session auth |
| Widget client | `backend/channels_app/widget_assets/app.js` (iframe served from backend origin) | multipart |

## Decided design

### 1. Storage backend

- Add `django-storages[s3]` (pulls `boto3`). Configure `STORAGES["default"]` as the S3 backend pointed at R2:
  - `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` (`local` / `main`).
  - Endpoint `https://<account_id>.r2.cloudflarestorage.com`, region `auto`, signature v4.
  - `querystring_auth=True`, `default_acl=None` (private), `file_overwrite=False`.
- R2 config is **required**: settings raise at startup if any R2 var is missing (except under tests, below).
- ★ **Guard against dev touching prod:** one R2 API token **per bucket**. Dev machines only ever hold the
  `local`-scoped token, and prod only holds the `main`-scoped one. The credentials enforce the boundary, not just
  the env var. Belt and braces: settings refuse `R2_BUCKET=main` when `DEBUG=True`.
- Remove `MEDIA_ROOT` from settings, compose files, `.env` examples and the `media` volume.
- **Tests** do not hit R2: test settings (or a test-runner override) use `InMemoryStorage` / temp filesystem storage,
  and the S3 backend must never be constructed under tests.
  Existing `override_settings(MEDIA_ROOT=...)` in tests are replaced accordingly.

### 2. Presigned uploads (browser → R2)

Three-step flow, same for every browser upload:

1. **Presign** — client `POST`s file metadata (`name`, `size`, `content_type`, one or many). Django checks auth and
   tenant access, size cap, allowed content types, creates the DB row in `pending` state with a server-chosen key
   (`<prefix>/<tenant>/<uuid>.<ext>`), and returns `{id, put_url, headers}` per file. `put_url` is a presigned PUT
   bound to that key and `Content-Type`, expiring in ~10 minutes.
2. **PUT** — browser uploads bytes directly to R2 (parallel for multiple files).
3. **Confirm** — client `POST`s the ids. Django `HEAD`s each key (exists, `ContentLength` ≤ cap) and queues the
   existing processing task. Missing/oversize → row marked failed, key deleted.

The worker then does the real validation (it must read bytes anyway): real image / supported doc, size, then the
existing pipeline (CLIP embed for catalog photos, ingestion for docs, catalog matching for chat photos). On failure it
deletes the R2 object and marks the row failed — same user-visible statuses as today.

Applies to:

| Upload | Presign authorised by | After confirm |
|---|---|---|
| Catalog photos | session user, KB admin (same check as today's upload) | existing image-embed task |
| Knowledge documents | session user, KB access (same as today) | existing ingestion job |
| Test-chat photo (dashboard) | session user, project access | message created with `attachment_info=[{"type":"image","path":key}]`, reply queued as today |
| Widget photo (anonymous visitor) | widget session/visitor token as today, rate-limited per visitor | same as test-chat, key under `widget_uploads/{widget.id}/` |

The old multipart endpoints are **replaced**, not kept. `_read_upload`'s prefix check stays and additionally must
verify the key belongs to that widget/project.

Keeping today's behaviour where the old flow relied on Django holding the bytes (*designer's calls*, marked ★):

- ★ **Duplicate documents** (today: SHA-256 → 409 `duplicate_file` in `knowledge/views.py` ~244-262). The browser
  computes SHA-256 (`crypto.subtle.digest`) and sends it with presign. Django runs the existing duplicate check and
  returns the same 409 before anything is uploaded. The hash is signed into the presigned PUT as
  `x-amz-checksum-sha256`, so R2 rejects the PUT if the bytes don't match, which means the client can't lie about it.
  If R2 turns out not to enforce the signed checksum, fall back to recomputing the hash in the ingestion worker and
  failing the doc as a duplicate.
- ★ **Catalog product + photos** (today: one transaction, `no_images` rejects a product without photos). The create
  presign call creates the product and its `pending` image rows together. The abandoned-upload cleanup also deletes
  a product left with zero images, so "no product without photos" still holds. Adding photos to an existing product
  uses the same presign → PUT → confirm steps against that product.
- ★ **Instant error for a bad chat photo** (today: widget/test chat return 400 "Send a JPEG, PNG or WebP image up to
  5 MB"). Confirm does a ranged GET of the first ~32 bytes and checks the image magic bytes (plus `HEAD` size) before
  creating the message, and returns the same 400 on failure. Full decode still happens in the worker. This is one
  tiny read, within the anti-goal.

Abandoned uploads (presigned, never confirmed): a Celery beat task deletes `pending` rows older than ~1 hour plus their
keys. An R2 lifecycle rule is not required (builder's call whether to add one as a backstop).

### 3. Presigned downloads (R2 → browser)

- `ProductImageFileView` keeps its permission check and returns **302 to a presigned GET URL** (~5 min expiry).
  Frontend `<img src>` and cookie auth stay unchanged.
- Any other place that serves stored files to browsers follows the same pattern (redirect after permission check).
- Worker reads keep using `default_storage.open()`; on R2 that is a worker-side download, no code change.

### 4. Inbound channel media (worker → R2)

- When an inbound Messenger/Instagram/WhatsApp message carries an **image or audio** attachment, a Celery task
  downloads it (reusing `media.py`'s SSRF-guarded Meta fetch: `_check_meta_url`, no redirects, size cap) and saves it
  to R2 under `chat_media/<project>/<uuid>.<ext>`, writing the key into that attachment's `path` in
  `Message.attachment_info`.
- `fetch_photo` then prefers the stored `path` when present, falling back to the Meta fetch.
- Audio: store only. Size cap 16 MB (WhatsApp's audio limit). Accept the audio MIME types Meta sends
  (ogg/opus, mp4/aac, mpeg, amr).
- Stickers, documents, locations etc. stay as today (metadata only).
- Download failures are logged and leave the attachment without `path`; they must never block or fail the reply path.

### 4b. Photo resizing ★

Applies to every stored photo: catalog, widget/test chat, inbound Meta. Not audio, not documents.

- **Worker (authoritative):** in the same worker step that validates the photo (it already holds the bytes; Pillow is
  installed), fix EXIF orientation, strip metadata, and write:
  - `display`: long edge ≤ 1600 px, WebP q≈80. This **replaces the original** in R2, so what's stored and sent is
    small. The original is not kept.
  - `thumb`: long edge ≤ 320 px, WebP, stored next to it as `<key-stem>_thumb.webp`.
  - CLIP embedding and catalog matching run on `display` (CLIP downsizes to 224 px anyway).
  - Images already under the limits are re-encoded (to strip metadata and use one format) but not upscaled.
- **Browser (optimisation only):** before the PUT, the dashboard and widget downscale to a long edge ≤ 2048 px with
  canvas / `createImageBitmap` so phone photos upload fast. Clients may skip it; the worker still enforces the size.
- **Serving:** download views take `?size=thumb` (default `display`) and redirect to that variant's presigned URL.
  The catalog grid (`ProductCatalog.tsx`) uses `thumb`.
- Deleting a photo deletes both keys.
- Limits (1600 / 320 / 2048, WebP quality) are settings constants, not per-tenant config.

### 5. Deletes

Existing `default_storage.delete()` calls (`accounts/views.py:265`, `knowledge/models.py:202,248`,
`knowledge/products.py:100`) work unchanged against R2.

### 6. Setup docs

Short doc (e.g. `docs/r2-storage.md`): create buckets `local` and `main`, create one API token scoped to each bucket, env vars,
and the **bucket CORS rule** allowing `PUT` (and `GET` for redirects) from:
- dashboard origins (`http://localhost:3000`, prod frontend domain)
- backend origin (the widget iframe is served from the backend)

with `Content-Type` as an allowed header.

## Left to the builder

- Exact endpoint paths/names for presign and confirm, and response shapes (follow existing `{"success", "data"}` style).
- Whether presign/confirm live in new views or extend the existing ones.
- How the pending state is modelled per type (reuse existing status fields where possible).
- Frontend upload helper shape (one shared `uploadToR2(files)` helper is expected, used by catalog, docs, test chat).
- Widget `app.js` changes to do the same three steps.
- Exact presign expiry values within the ranges above.
