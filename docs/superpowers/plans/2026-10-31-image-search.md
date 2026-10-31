# Product Image Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tenants upload products (name, description, photos) to a knowledge base. When a customer sends a photo on Facebook, Instagram, WhatsApp, the website widget or the dashboard test chat, the agent finds the most visually similar catalog products in Qdrant and answers the customer's message with them.

The feature has three switches: a platform master switch, a per-tenant switch and a per-channel switch. Every product photo indexed and every customer photo searched is metered.

**Architecture:** Product photos are embedded with CLIP (`Qdrant/clip-ViT-B-32-vision` via fastembed, which is already installed, 512-dim) into one Qdrant collection per knowledge base, `{kb.vector_collection}_images`, the same way `_web` already works. At reply time, `process_reply` annotates each photo message in the turn with its catalog matches (`meta["photo_matches"]`). `format_message_with_context` renders that note, so both the LLM and the existing text search see it without any change to `generate_reply`'s signature.

**Tech Stack:** Django 5 / DRF, Celery, qdrant-client 1.15.1, fastembed 0.7.3 `ImageEmbedding`, Pillow (already a transitive dependency), Next.js 15 dashboard, and the vanilla-JS widget.

**Spec:** None. The user skipped brainstorming, so the decisions below are the planner's calls and serve as the spec. The user asked for the three additions: test-chat upload, per-tenant switch and usage metering.

## Design decisions (planner's calls; review these first)

1. **Three switches.** The feature is live for a message only if all three are on:
   - **Master:** the `IMAGE_SEARCH_ENABLED` environment boolean, default `False`. It is the platform kill switch.
   - **Tenant**, two fields on `Project`; the operator's wins:
     - `image_search_allowed`, default `False`. Set by the **platform operator** in Django admin (an editable column in the Project list). It gates plans, and tenants can never change it.
     - `image_search_enabled`, default `True`. Set by **tenant admins** on the dashboard Settings page. It only matters while `image_search_allowed` is true, so allowing a tenant makes the feature available at once, and it still does nothing until they switch a channel on.
     - Reads go through two properties:
       - `Project.image_search_available` = `settings.IMAGE_SEARCH_ENABLED and self.image_search_allowed`
       - `Project.image_search_on` = `self.image_search_available and self.image_search_enabled`
   - **Channel:** `image_search_enabled` on `ChannelCredential` (FB/IG/WA) and on `Widget`, default `False`. Tenant admins flip it with an "Image search: On/Off" button per channel or widget row.
    - The toggle is per connection, not per platform: two Facebook pages in one tenant each have their own switch.
     - The dashboard test chat has no channel row; it is on whenever the tenant is on.
   - **When master or tenant is off:**
     - product and photo-upload endpoints answer 404 `feature_disabled`;
     - inbound photos stay unanswered, as they are today;
     - the dashboard hides the catalog, the toggles and the photo buttons.
     - The dashboard learns the state from `GET /api/kb/features/?project_id=` → `{"image_search_available": project.image_search_available, "image_search": project.image_search_on}`.
     - Tenant admins flip their switch with `PATCH /api/kb/features/` `{"project_id", "image_search": bool}`. While not available this answers 403 `not_allowed` and changes nothing.
2. **Catalog shape:** `Product(kb, name, description)` plus `ProductImage(product, file, status)`. Description is free text; whatever the tenant writes there (price, SKU, link) is what the agent can quote. There are no structured fields (YAGNI).
3. **Matching:**
   - Results are grouped by product (`query_points_groups`, group_by `product_id`), keeping the top 3 with cosine ≥ `IMAGE_SEARCH_MIN_SCORE` (default `0.80`, a calibration knob).
   - The Qdrant payload holds ids only (`product_id`, `image_id`). Names and descriptions are read from the DB at search time, so renames apply immediately and deleted products never match.
4. **Reply:** text only. The agent describes the matched product(s); sending product photos back is out of scope.
5. **Channels:**
   - Facebook, Instagram and WhatsApp: photos are fetched from Meta.
   - Widget and dashboard test chat: photos are uploaded to our storage and read back from there.
   - Uploaded photos render as `📷 Photo` plus their text, with no thumbnail.
6. **Usage metering:**
   - Each event is a new `UsageEvent` kind, `"image"`, with `tokens=0` and `metadata={"op": "index" | "search", ...}`.
   - `tokens=0` keeps the token totals and the daily token trend in analytics unchanged, since those sum `tokens` across all kinds. Photo usage is counted as rows instead.
   - Analytics gains `photos_indexed` and `photo_searches` for the selected period.
   - Neither event counts toward `monthly_token_quota`.
7. **Limits:**
   - JPEG/PNG/WEBP only, ≤ 5 MB, ≤ 40 megapixels.
   - ≤ 10 photos per product; ≤ 3 photos analysed per customer message.
   - Facebook/Instagram media is only downloaded from Meta CDN hosts (SSRF guard).
8. **Stickers:** Facebook stickers and the like button arrive as `type: "image"` with a `sticker_id`; they are not photos and are ignored.

## Global Constraints

- Settings, all in `backend/core/settings.py`:
  - `IMAGE_SEARCH_ENABLED = env.bool("IMAGE_SEARCH_ENABLED", default=False)`
  - `IMAGE_EMBEDDING_MODEL = env("IMAGE_EMBEDDING_MODEL", default="Qdrant/clip-ViT-B-32-vision")`
  - `IMAGE_SEARCH_MIN_SCORE = env.float("IMAGE_SEARCH_MIN_SCORE", default=0.80)`
- Add all three to `.env.example`, commented, with the defaults.
- Every feature check uses `project.image_search_on`. Never read `settings.IMAGE_SEARCH_ENABLED` directly outside `Project.image_search_on`.
- Image collection name: `f"{kb.vector_collection}_images"`, exposed as `KnowledgeBase.image_collection`.
- `MAX_IMAGE_BYTES = 5 * 1024 * 1024`, `MAX_IMAGE_PIXELS = 40_000_000`, `MAX_IMAGES_PER_PRODUCT = 10`, `MAX_PHOTOS_PER_MESSAGE = 3`, `IMAGE_SEARCH_TOP_K = 3`.
- API envelope as everywhere else: `{"success": true, "data": ...}` / `{"success": false, "error": {"code", "message"}}`.
- Tests use the Django runner, run from the repo root: `docker compose run --rm django python manage.py test <dotted.module> -v 2`.
- The Dockerfile sets `HF_HUB_OFFLINE=1`, so the CLIP model must be baked into the image (Task 2). Otherwise the workers fail at the first photo.
- The code style is tabs in new backend files, matching `channels_app/` and `agents/`. `knowledge/views.py` uses spaces; new knowledge files use tabs like `knowledge/suggestions.py`.

## Review Focus

1. **Facebook like and stickers on an enabled channel.** They must not trigger a reply, an image search or a usage event. Tests: `test_sticker_is_not_a_photo` (Task 6) and `test_sticker_is_ignored` (Task 7).
2. **Unreadable or failed photos.** A corrupt, non-image or oversized photo, or a CDN fetch that fails at reply time, must still get a reply, with the note "could not be checked". The failure must not cause retries or an empty reply, and no `search` usage event is recorded. Test in Task 7: `test_unreadable_photo_still_replies`.
3. **Tenant off while master and channel are on.** "Tenant off" means either the operator disallowed it or the tenant switched it off. The photo stays unanswered, product endpoints answer 404 `feature_disabled`, and test-chat upload answers 404. The same holds with master off. Tests: `test_tenant_off_hides_endpoints` (Task 4), `test_tenant_off_ignores_photo` (Task 7), `test_test_chat_photo_tenant_off_404` (Task 8).
   - **Operator priority:** a tenant admin cannot turn the feature on while the operator has it disallowed, and the operator revoking it stops photo replies at once, whatever the tenant's switch says. Tests in Task 4: `test_tenant_cannot_enable_when_not_allowed` and `test_operator_revoke_overrides_tenant`.
4. **Empty catalog.** With no products, so no collection exists yet, the customer gets the "no catalog product looks similar" note, not an error. Test in Task 2: `test_search_missing_collection_returns_empty`.
5. **Cross-tenant access.** Product, image, image-file and test-chat conversation ids from another project answer 404, and a non-admin cannot write to the catalog. Tests: `test_other_project_product_is_404` (Task 4), `test_test_chat_photo_other_project_404` (Task 8).

---

### Task 1: Tenant switch and photo usage metering

**Files:**
- Modify:
  - `backend/core/settings.py` (the three settings above, next to `QDRANT_*` at :139-150)
  - `.env.example`
  - `backend/accounts/models.py:19-26` (`Project`)
  - `backend/accounts/admin.py` (`ProjectAdmin`)
  - `backend/usage/models.py:140` (`KIND_CHOICES`)
  - `backend/usage/utils.py`
  - `backend/analytics/views.py:195-210`
- Create, via `makemigrations accounts usage`:
  - `backend/accounts/migrations/0003_project_image_search.py`
  - `backend/usage/migrations/0002_usageevent_image_kind.py`
- Test: `backend/usage/tests/test_image_usage.py` (create the `tests/` package if absent)

**Interfaces:**
- Produces:
  - `Project.image_search_allowed = models.BooleanField(default=False)`: operator only.
  - `Project.image_search_enabled = models.BooleanField(default=True)`: tenant's own switch.
  - `Project.image_search_available -> bool` (property): `settings.IMAGE_SEARCH_ENABLED and self.image_search_allowed`.
  - `Project.image_search_on -> bool` (property): `self.image_search_available and self.image_search_enabled`.
  - `ProjectAdmin`:
    - add both fields to `list_display` and `list_filter`;
    - add `image_search_allowed` to `list_editable`;
    - make `image_search_enabled` read-only in admin (`readonly_fields`), so the operator sees the tenant's choice but steers only through `allowed`.
  - `UsageEvent.KIND_CHOICES` gains `("image", "Image")`.
  - `record_image_usage(project_id: str, op: str, **metadata) -> None` in `usage/utils.py`:
    - creates `UsageEvent(kind="image", tokens=0, metadata={"op": op, **metadata})`;
    - logs and swallows any exception, as `track_token_usage` does;
    - `op` is `"index"` or `"search"`.
  - The analytics response gains `"photos_indexed"` (count of `kind="image", metadata__op="index"`) and `"photo_searches"` (count of `op="search"`), for the same `usage_events_qs` period. Put them next to `total_embedding_tokens`.

- [ ] **Step 1: Write the failing tests**

```python
def test_image_search_on_needs_master_operator_and_tenant(self):
	p = Project.objects.create(name="P", slug="p", image_search_allowed=True)  # enabled defaults True
	with override_settings(IMAGE_SEARCH_ENABLED=True): self.assertTrue(p.image_search_on)
	with override_settings(IMAGE_SEARCH_ENABLED=False): self.assertFalse(p.image_search_on)
	p.image_search_enabled = False  # tenant off
	with override_settings(IMAGE_SEARCH_ENABLED=True):
		self.assertFalse(p.image_search_on); self.assertTrue(p.image_search_available)
	p.image_search_enabled, p.image_search_allowed = True, False  # operator off wins
	with override_settings(IMAGE_SEARCH_ENABLED=True):
		self.assertFalse(p.image_search_on); self.assertFalse(p.image_search_available)

def test_defaults(self):  # new Project: allowed False, enabled True

def test_record_image_usage(self):
	record_image_usage(str(p.id), "search", message_id="m1")
	e = UsageEvent.objects.get()
	self.assertEqual((e.kind, e.tokens, e.metadata), ("image", 0, {"op": "search", "message_id": "m1"}))

def test_record_image_usage_never_raises(self):  # project_id=str(uuid4()) (FK violation) -> no exception

def test_analytics_counts_photos_without_changing_tokens(self):
	# 2 index + 3 search events, 1 chat_in tokens=100 -> photos_indexed 2, photo_searches 3, total_tokens 100
```

Find the analytics URL and login pattern in `backend/analytics/` tests or views.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `docker compose run --rm django python manage.py test usage.tests.test_image_usage -v 2`

Expected: errors on `image_search_enabled` and `record_image_usage`.

- [ ] **Step 3: Implement the fields, the migrations, the admin, `record_image_usage` and the analytics counts**

Because of the FK violation `record_image_usage` swallows, wrap the create in `transaction.atomic()` so a failure inside a caller's transaction does not poison it.

- [ ] **Step 4: Run the tests and confirm they pass**

Same command, plus `accounts.tests`. Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/core/settings.py .env.example backend/accounts backend/usage backend/analytics/views.py
git commit -m "feat(image-search): per-tenant switch and photo usage metering"
```

---

### Task 2: Image embedding and Qdrant image collection

**Files:**
- Create: `backend/agents/image_search.py`
- Modify: `backend/Dockerfile:24-25` (bake the model next to the bm25 bake line)
- Test: `backend/agents/tests/test_image_search.py`

**Interfaces:**
- Produces:
  - `class NotAnImage(ValueError)`
  - `load_image(data: bytes) -> PIL.Image.Image`: raises `NotAnImage` when the data is over `MAX_IMAGE_BYTES`, is not JPEG/PNG/WEBP, is over `MAX_IMAGE_PIXELS`, or is undecodable. Returns an RGB image.
  - `image_format(data: bytes) -> str`: `"jpg" | "png" | "webp"`, after `load_image` succeeds.
  - `embed_image(img: PIL.Image.Image) -> list[float]`: lazy, lock-guarded fastembed `ImageEmbedding(settings.IMAGE_EMBEDDING_MODEL)` singleton, same pattern as `get_sparse_embeddings`.
  - `upsert_image(collection: str, image_id: str, vector: list[float], product_id: str) -> None`: creates the collection (size `len(vector)`, COSINE) if missing; point id = `image_id`; payload `{"product_id", "image_id"}`.
  - `search_products(collection: str, vector: list[float]) -> list[tuple[str, float]]`: `[(product_id, score)]`, best first, ≤ `IMAGE_SEARCH_TOP_K`, score ≥ `settings.IMAGE_SEARCH_MIN_SCORE`. Returns `[]` when the collection does not exist.
  - `delete_images(collection: str, image_ids: list[str]) -> None`: no-op when the collection is missing.
  - Constants `MAX_IMAGE_BYTES`, `MAX_IMAGE_PIXELS`, `IMAGE_SEARCH_TOP_K`.

- [ ] **Step 1: Write the failing tests**

Patch `agents.image_search.get_qdrant_client` to return one shared `QdrantClient(":memory:")`, as `test_hybrid_search.py:13-22` does. Use hand-made 4-dim vectors and never load the real model.

```python
def test_search_groups_by_product_and_filters_by_score(self):
	upsert_image("c_images", A1, [1, 0, 0, 0], "pA")
	upsert_image("c_images", A2, [0.9, 0.1, 0, 0], "pA")
	upsert_image("c_images", B1, [0.7, 0.7, 0, 0], "pB")
	upsert_image("c_images", C1, [0, 0, 1, 0], "pC")
	with override_settings(IMAGE_SEARCH_MIN_SCORE=0.6):
		hits = search_products("c_images", [1, 0, 0, 0])
	self.assertEqual([p for p, _ in hits], ["pA", "pB"])  # pA once, pC below threshold
	self.assertAlmostEqual(hits[0][1], 1.0, places=3)

def test_search_missing_collection_returns_empty(self):
	self.assertEqual(search_products("nope_images", [1, 0, 0, 0]), [])

def test_delete_images_removes_points(self): ...  # upsert A1, delete [A1], search -> []

def test_load_image_rejects_non_images_and_oversize(self):
	with self.assertRaises(NotAnImage): load_image(b"%PDF-1.4 ...")
	with self.assertRaises(NotAnImage): load_image(b"\0" * (MAX_IMAGE_BYTES + 1))
	png = _png_bytes(4, 4)  # helper: Pillow-encoded tiny PNG
	self.assertEqual(load_image(png).mode, "RGB")
	self.assertEqual(image_format(png), "png")
```

Point ids (`A1` and so on) must be uuid strings.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `docker compose run --rm django python manage.py test agents.tests.test_image_search -v 2`

Expected: ImportError for `agents.image_search`.

- [ ] **Step 3: Implement `agents/image_search.py`**

- Reuse `agents.vector_store.get_qdrant_client`.
- Grouping uses `client.query_points_groups(collection, query=vector, group_by="product_id", limit=IMAGE_SEARCH_TOP_K, group_size=1, score_threshold=settings.IMAGE_SEARCH_MIN_SCORE)`.
- `load_image`:
  - checks length first;
  - then `Image.open(BytesIO(data))`;
  - rejects `img.format not in {"JPEG", "PNG", "WEBP"}` and `width*height > MAX_IMAGE_PIXELS` before calling `.convert("RGB")`;
  - converts any `PIL.UnidentifiedImageError`/`OSError` into `NotAnImage`.
- `embed_image`: `list(model.embed([img]))[0].tolist()`. If fastembed 0.7.3 rejects PIL input, save it to a `tempfile.NamedTemporaryFile(suffix=".png")` and pass the path.
- Add a `ponytail:` comment on the singleton: in-process CLIP model; move to a model server like TEI if worker memory hurts.

- [ ] **Step 4: Run the tests and confirm they pass**

Same command. Expected: 4 tests OK.

- [ ] **Step 5: Bake the model into the image and smoke-test it**

Add to the Dockerfile's model-bake `RUN`: `python -c "from fastembed import ImageEmbedding; ImageEmbedding('Qdrant/clip-ViT-B-32-vision')"`. It runs with the existing `FASTEMBED_CACHE_PATH`, before `HF_HUB_OFFLINE=1`.

Run:

```
docker compose build django
docker compose run --rm django python -c "from agents.image_search import load_image, embed_image; from PIL import Image; import io; b=io.BytesIO(); Image.new('RGB',(64,64),'red').save(b,'PNG'); print(len(embed_image(load_image(b.getvalue()))))"
```

Expected: `512`.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/image_search.py backend/agents/tests/test_image_search.py backend/Dockerfile
git commit -m "feat(image-search): CLIP image embeddings and per-KB image collection"
```

---

### Task 3: Product models and the embedding task

**Files:**
- Modify:
  - `backend/knowledge/models.py`: add `image_collection` property to `KnowledgeBase`; add the `Product` and `ProductImage` models after `Document`.
  - `backend/knowledge/tasks.py`: add `embed_product_image`.
  - `backend/accounts/views.py:257-262`: also delete `kb.image_collection`.
- Create: `backend/knowledge/migrations/0014_product_productimage.py` (via `makemigrations`)
- Test: `backend/knowledge/tests/test_products.py`

**Interfaces:**
- Consumes:
  - Task 2 `load_image`, `embed_image`, `upsert_image`, `delete_images`;
  - Task 1 `record_image_usage`.
- Produces:
  - `KnowledgeBase.image_collection -> str`: `f"{self.vector_collection}_images"`.
  - `Product`:
    - `id` UUID pk
    - `kb` FK `KnowledgeBase` CASCADE, related_name `products`
    - `name` CharField(255)
    - `description` TextField(blank)
    - `created_at`, `updated_at`
    - `delete()` removes the image points and files, then the row.
  - `ProductImage`:
    - `id` UUID pk
    - `product` FK CASCADE, related_name `images`
    - `file` FileField(max_length=255)
    - `status` CharField choices `pending | ready | failed`, default `pending`
    - `error` CharField(255, blank)
    - `created_at`
    - `delete()` removes its point and file.
  - `product_image_path(kb, image_id: str, ext: str) -> str`: `f"products/{kb.project.slug}/{kb.id.hex}/{image_id}.{ext}"`.
  - `MAX_IMAGES_PER_PRODUCT = 10` in `knowledge/models.py`.
  - Celery `embed_product_image(image_id: str) -> None`:
    - reads the file through `default_storage`;
    - calls `upsert_image(kb.image_collection, image_id, vector, product_id)`;
    - sets `ready` and calls `record_image_usage(project_id, "index", image_id=image_id)`;
    - on any exception sets `failed` with `error=type(exc).__name__` and records no usage;
    - a missing row is a no-op (deleted while queued).

- [ ] **Step 1: Write the failing tests**

Use `override_settings(MEDIA_ROOT=tempfile.mkdtemp())` as in `test_suggestions.py:13-21`. Patch `knowledge.tasks.embed_image` to return `[1, 0, 0, 0]` and `knowledge.tasks.upsert_image` / `knowledge.models.delete_images` with mocks.

```python
def test_embed_marks_ready_upserts_and_meters(self):
	embed_product_image(str(self.image.id))
	self.image.refresh_from_db()
	self.assertEqual(self.image.status, "ready")
	self.upsert.assert_called_once_with(self.kb.image_collection, str(self.image.id), [1, 0, 0, 0], str(self.product.id))
	self.assertEqual(UsageEvent.objects.filter(kind="image", metadata__op="index").count(), 1)

def test_embed_failure_marks_failed(self):  # embed_image raises RuntimeError
	...; self.assertEqual((self.image.status, self.image.error), ("failed", "RuntimeError"))
	# and no UsageEvent

def test_deleted_image_is_noop(self):  # call with uuid4(): no exception, upsert not called

def test_product_delete_removes_points_and_files(self):
	path = self.image.file.name
	self.product.delete()
	self.delete_images.assert_called_once_with(self.kb.image_collection, [str(self.image.id)])
	self.assertFalse(default_storage.exists(path))
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `docker compose run --rm django python manage.py test knowledge.tests.test_products -v 2`

Expected: ImportError, `Product` is not defined.

- [ ] **Step 3: Implement the models, `makemigrations knowledge`, the task and the project-delete line**

`Product.delete` collects the image ids first, calls `delete_images` once, deletes the files, then calls `super().delete()`. The cascade does not call `ProductImage.delete()`, which is why `Product.delete` does this itself.

Add a `ponytail:` note: no `reindex_vectors` support for image collections; re-queue `embed_product_image` for all images if the CLIP model changes.

- [ ] **Step 4: Run the tests and confirm they pass**

Same command, plus `accounts.tests.test_account`. Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/knowledge/models.py backend/knowledge/tasks.py backend/knowledge/migrations/0014_product_productimage.py backend/accounts/views.py backend/knowledge/tests/test_products.py
git commit -m "feat(image-search): product catalog models and image embedding task"
```

---

### Task 4: Product catalog API and features endpoint

**Files:**
- Create: `backend/knowledge/products.py` (views, like `knowledge/suggestions.py`)
- Modify: `backend/knowledge/urls.py`
- Test: `backend/knowledge/tests/test_product_api.py`

**Interfaces:**
- Consumes:
  - Task 3 models, `product_image_path`, `embed_product_image`.
  - Task 2 `load_image`, `image_format`, `NotAnImage`.
  - Task 1 `Project.image_search_on`.
  - `knowledge.views._project_user(user, project_id, admin=...)` and `_resolve_kb(project, kb_id)`.
- Produces routes, all under `api/kb/` and all `IsAuthenticated`:

| Method + path | Who | Behaviour |
|---|---|---|
| `GET features/?project_id` | member | `{"image_search_available": project.image_search_available, "image_search": project.image_search_on}` (never feature-gated) |
| `PATCH features/` JSON `{project_id, image_search: bool}` | admin | sets `project.image_search_enabled`; 403 `not_allowed` with no change when `not project.image_search_available`; returns the GET shape |
| `GET products/?project_id&kb_id` | member | `[{id, name, description, created_at, images: [{id, status, url}]}]`; `url` = `/api/kb/products/images/<id>/file/` |
| `POST products/` multipart `project_id, kb_id, name, description, images` (getlist) | admin | 201 with the product dict |
| `PATCH products/<uuid:product_id>/` JSON `name?, description?` | admin | updates the product |
| `DELETE products/<uuid:product_id>/` | admin | deletes the product |
| `POST products/<uuid:product_id>/images/` multipart `images` | admin | adds photos |
| `DELETE products/images/<uuid:image_id>/` | admin | deletes one photo |
| `GET products/images/<uuid:image_id>/file/` | member | `FileResponse` with the stored content type |

- Every route except `features/` returns 404 `feature_disabled` when `not project.image_search_on`, after the access check.
- A product or image outside the user's projects returns 404 `not_found`.
- Validation errors:
  - `name` empty → 400 `invalid_name`
  - no images on create → 400 `no_images`
  - more than `MAX_IMAGES_PER_PRODUCT` in total → 400 `too_many_images`
  - any file rejected by `load_image` → 400 `invalid_image`, with the filename in the message
- Uploads save every file only after all pass validation, then call `embed_product_image.delay(str(img.id))` for each.

- [ ] **Step 1: Write the failing tests**

Use the `APIClient` login pattern from `knowledge/tests/test_document_upload.py`. Patch `knowledge.products.embed_product_image`. Use `MEDIA_ROOT` tempdir, `@override_settings(IMAGE_SEARCH_ENABLED=True)` on the class, and `image_search_allowed=True` on the test project.

```python
def test_create_product_with_images(self):  # 201, 2 ProductImage rows pending, delay called twice
def test_invalid_image_rejects_whole_upload(self):  # one png + one b"not an image" -> 400 invalid_image, 0 products, 0 files
def test_too_many_images(self):  # 11 pngs -> 400 too_many_images
def test_list_shows_images_with_url(self):
def test_patch_and_delete(self):
def test_image_file_served_to_member(self):  # 200, content == stored bytes
def test_other_project_product_is_404(self):  # user B: GET file, PATCH, DELETE -> 404
def test_non_admin_cannot_write(self):  # ProjectUser role!="admin": POST -> 403
def test_tenant_off_hides_endpoints(self):  # project.image_search_enabled=False: GET products -> 404 feature_disabled; features -> {"image_search_available": True, "image_search": False}
def test_tenant_admin_toggles_feature(self):  # PATCH features {image_search: False} -> 200, DB False; PATCH True -> back on
def test_tenant_non_admin_cannot_toggle(self):  # role!="admin" -> 403
def test_tenant_cannot_enable_when_not_allowed(self):  # image_search_allowed=False, enabled=False: PATCH {image_search: True} -> 403 not_allowed, enabled stays False
def test_operator_revoke_overrides_tenant(self):  # enabled=True, then allowed=False -> GET products 404 feature_disabled; features -> {"image_search_available": False, "image_search": False}
@override_settings(IMAGE_SEARCH_ENABLED=False)
def test_master_off_hides_endpoints(self):  # tenant on, master off -> same as above
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `docker compose run --rm django python manage.py test knowledge.tests.test_product_api -v 2`

Expected: 404s and ImportError.

- [ ] **Step 3: Implement `knowledge/products.py` and the URL entries**

- [ ] **Step 4: Run the tests and confirm they pass**

Same command, plus the existing `knowledge.tests`. Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/knowledge/products.py backend/knowledge/urls.py backend/knowledge/tests/test_product_api.py
git commit -m "feat(image-search): product catalog API behind master and tenant switches"
```

---

### Task 5: Per-channel toggles

**Files:**
- Modify:
  - `backend/channels_app/models.py`: add `image_search_enabled = models.BooleanField(default=False)` to `ChannelCredential` (after `is_active` at :579) and to `Widget` (after `is_active`).
  - `backend/channels_app/api.py`: the channel dict in the list/detail responses, and PATCH at :785-817.
  - `backend/channels_app/widget.py`: `_apply` at :106-130 and `_widget_dict` at :63.
- Create: `backend/channels_app/migrations/0017_image_search_enabled.py`
- Test: `backend/channels_app/tests/test_image_search_toggle.py`

**Interfaces:**
- Produces:
  - `ChannelCredential.image_search_enabled: bool` and `Widget.image_search_enabled: bool`;
  - both are in the API JSON and writable via PATCH `{"image_search_enabled": bool}`.
  - The toggle stays writable even when the tenant is off; it simply has no effect then.

- [ ] **Step 1: Write the failing tests**

```python
def test_channel_patch_toggles_image_search(self):  # PATCH -> 200, field True in response + DB
def test_widget_patch_toggles_image_search(self):
def test_defaults_off(self):  # new channel and widget -> False
```

- [ ] **Step 2: Run and confirm they fail.** Run: `docker compose run --rm django python manage.py test channels_app.tests.test_image_search_toggle -v 2`

- [ ] **Step 3: Add the fields, run `makemigrations channels_app`, and update the PATCH handlers and the dicts** (`bool(data["image_search_enabled"])`, as `is_active` does)

- [ ] **Step 4: Run and confirm they pass**, along with `channels_app.tests.test_widget`.

- [ ] **Step 5: Commit** (`feat(image-search): per-channel image search toggle`)

---

### Task 6: Fetching and storing customer photos

**Files:**
- Create: `backend/channels_app/media.py`
- Test: `backend/channels_app/tests/test_media.py`

**Interfaces:**
- Consumes:
  - `tasks.get_whatsapp_credential`;
  - `facebook_client.GRAPH_API_URL`;
  - Task 2 `load_image`, `image_format`, `MAX_IMAGE_BYTES`.
- Produces:
  - `is_photo(att: dict) -> bool`: `att.get("type") == "image" and not (att.get("payload") or {}).get("sticker_id")`.
  - `store_photo(data: bytes, folder: str) -> str`:
    - validates with `load_image`, which raises `NotAnImage`;
    - saves via `default_storage.save(f"{folder}/{uuid4().hex}.{image_format(data)}", ContentFile(data))`;
    - returns the saved path.
  - `fetch_photo(msg: Message, att: dict) -> bytes`: raises `MediaError(Exception)` on any failure. By channel (`msg.conversation.channel`):
    - **facebook / instagram:** GET `att["url"]` only if `https` and the host ends with one of `META_CDN_SUFFIXES = (".fbcdn.net", ".fbsbx.com", ".cdninstagram.com")`, otherwise `MediaError`.
    - **whatsapp:** GET `{GRAPH_API_URL}/{att["payload"]["id"]}` with `Bearer creds.access_token` to get JSON `url`, then GET that url with the same header.
    - **widget / self:** `default_storage.open(att["path"]).read()`.
    - **Every download:** `stream=True`, `timeout=10`, stop and raise once more than `MAX_IMAGE_BYTES` bytes have been read.

- [ ] **Step 1: Write the failing tests** (mock `channels_app.media.requests.get`)

```python
def test_sticker_is_not_a_photo(self):
	self.assertFalse(is_photo({"type": "image", "payload": {"sticker_id": 369239263222822, "url": "https://scontent.xx.fbcdn.net/x"}}))
	self.assertTrue(is_photo({"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/x"}}))
def test_facebook_rejects_non_meta_host(self):  # url http://169.254.169.254/ -> MediaError, requests.get not called
def test_whatsapp_two_step_fetch_uses_token(self):  # both calls carry Authorization header; returns 2nd body
def test_oversize_download_raises(self):  # iter_content yields MAX_IMAGE_BYTES+1 bytes -> MediaError
def test_store_photo_round_trip(self):  # MEDIA_ROOT tmp: store png under "chat_uploads/x" -> path endswith .png; fetch_photo for a "self" msg returns same bytes
def test_store_photo_rejects_non_image(self):  # NotAnImage, nothing saved
```

- [ ] **Step 2: Run and confirm they fail.** Run: `docker compose run --rm django python manage.py test channels_app.tests.test_media -v 2`

- [ ] **Step 3: Implement `channels_app/media.py`**

- [ ] **Step 4: Run and confirm they pass**

- [ ] **Step 5: Commit** (`feat(image-search): fetch and store customer photos per channel`)

---

### Task 7: Answering photos in the reply pipeline

**Files:**
- Create: `backend/channels_app/image_reply.py`
- Modify:
  - `backend/channels_app/service/facebook_messing.py:177`
  - `backend/channels_app/service/whatsapp_messaging.py:44-50`
  - `backend/channels_app/reply.py:122-126` (`_newer_text_message`) and `_reply` at :159
  - `backend/channels_app/tasks.py:41-43` (`format_message_with_context`)
  - `backend/agents/llm.py:65-73` (one RESPONSE RULES line)
- Test: `backend/channels_app/tests/test_image_reply.py`

**Interfaces:**
- Consumes:
  - Task 6 `is_photo`, `fetch_photo`, `MediaError`;
  - Task 2 `load_image`, `embed_image`, `search_products`, `NotAnImage`;
  - Task 3 `Product`, `KnowledgeBase.image_collection`;
  - Task 5 toggle fields;
  - Task 1 `Project.image_search_on`, `record_image_usage`.
- Produces:
  - `wants_photo_reply(project, attachment_info: list, channel_enabled: bool) -> bool`: `project.image_search_on and channel_enabled and any(is_photo(a) for a in attachment_info)`.
  - `annotate_turn(conversation, turn: list[Message]) -> None`:
    - for each message with `meta.get("photo_reply")` and no `meta.get("photo_matches")`, analyse up to `MAX_PHOTOS_PER_MESSAGE = 3` photos;
    - after each successful search (embedding done and Qdrant answered, match or no match), call `record_image_usage(project_id, "search", message_id=str(m.id), matched=bool(hits))`;
    - set `m.meta["photo_matches"]` (str) **in place** and save with `update_fields=["meta"]`;
    - it never raises.
  - Inbound handlers set `meta["photo_reply"] = True` and status `pending`, then call `schedule_reply`, when `wants_photo_reply(...)` holds. Facebook/Instagram use `cred.image_search_enabled`, where `cred` is already resolved in `handle_facebook_message`; WhatsApp uses `cred.image_search_enabled`. Widget and test chat set the flag in Task 8.
  - WhatsApp photo messages use `message["image"].get("caption", "")` as `text` when `wants_photo_reply` holds.

Exact text of `meta["photo_matches"]`, one bracket per photo, space-joined:
- Matches: `[Customer sent a photo. Closest catalog products: "{name}" (similarity {score:.2f}): {description[:300]}; "{name2}" (similarity {score:.2f}): {description[:300]}]`
- No match, or no collection: `[Customer sent a photo. No catalog product looks similar.]`
- `MediaError`, `NotAnImage` or any search error: `[Customer sent a photo, but it could not be checked against the catalog.]` (log with `logger.warning`)

`format_message_with_context`: when `meta.get("photo_matches")`, append it in place of the generic `[Message includes attachments: ...]` note. History then keeps which product was matched.

`_newer_text_message` filter becomes `(Q(has_attachments=False) & ~Q(text="")) | Q(meta__photo_reply=True)`, because photo messages now have their own reply task. Update the docstring to match.

In `_reply`, call `image_reply.annotate_turn(conversation, turn)` right after the `is_paused` check and before building `question`. A Celery retry finds `photo_matches` already saved and does not fetch, search or meter again.

New RESPONSE RULES line in `build_system_prompt` (exact copy):
`"- A message may note that the customer sent a photo and list the closest catalog products with a similarity score; answer about the best match, mention others only if their scores are close, and if no product matched or the photo could not be checked, say you couldn't identify it and ask for the product name or a clearer photo\n"`

- [ ] **Step 1: Write the failing tests**

Use `PipelineBase` from `test_reply_pipeline.py:20-43`. Patch:
- `channels_app.image_reply.fetch_photo` → PNG bytes;
- `channels_app.image_reply.embed_image` → `[1, 0, 0, 0]`;
- `channels_app.image_reply.search_products` → `[(str(product.id), 0.93)]`.

Use `@override_settings(IMAGE_SEARCH_ENABLED=True)`, `image_search_allowed=True` on the project, and `image_search_enabled=True` on the channel.

```python
def test_photo_message_gets_reply_with_match(self):
	# FB webhook payload with one image attachment
	# -> generate_reply called once; its question contains '"Red Kurta" (similarity 0.93)'
	# -> one UsageEvent(kind="image", metadata__op="search")
def test_no_match_note(self):  # search_products -> [] ; question contains "No catalog product looks similar."; search still metered
def test_unreadable_photo_still_replies(self):  # fetch_photo raises MediaError; reply sent; note "could not be checked"; no UsageEvent
def test_sticker_is_ignored(self):  # sticker attachment -> no schedule_reply, status completed, no UsageEvent
def test_channel_off_ignores_photo(self):  # channel image_search_enabled=False -> no reply (today's behaviour)
def test_toggle_is_per_connection(self):  # one project, two FB pages (two ChannelCredentials): page A on, page B off -> photo to A answered, photo to B not
def test_tenant_off_ignores_photo(self):  # project.image_search_enabled=False -> no reply
def test_operator_disallowed_ignores_photo(self):  # project.image_search_allowed=False (tenant enabled) -> no reply
@override_settings(IMAGE_SEARCH_ENABLED=False)
def test_master_off_ignores_photo(self):
def test_whatsapp_caption_is_text(self):  # WA image with caption "price?" -> question starts with "price?"
def test_photo_then_text_burst_answers_once(self):  # photo msg then text msg before reply -> one reply covering both
def test_retry_does_not_refetch(self):  # meta photo_matches pre-set -> fetch_photo not called, no new UsageEvent
```

- [ ] **Step 2: Run and confirm they fail.** Run: `docker compose run --rm django python manage.py test channels_app.tests.test_image_reply -v 2`

- [ ] **Step 3: Implement `image_reply.py` and the handler, `reply.py`, `tasks.py` and `llm.py` edits**

In `annotate_turn`, load products with `Product.objects.filter(id__in=ids, kb=conversation.knowledge_base)` and keep the score order. The KB filter guards against stale points.

- [ ] **Step 4: Run the new tests and the whole `channels_app` and `agents` suites**

Run: `docker compose run --rm django python manage.py test channels_app agents -v 2`

Expected: all OK, including the existing `test_reply_pipeline`, `test_whatsapp`, `test_instagram`, `test_facebook_webhook` (the pipeline must be unchanged for text and for photo messages when any switch is off).

- [ ] **Step 5: Commit** (`feat(image-search): answer customer photos with catalog matches`)

---

### Task 8: Photo upload in the widget and the dashboard test chat

**Files:**
- Modify:
  - `backend/channels_app/widget.py`: `WidgetSessionView.post` at :262, `_visitor_message` at :269, and a new `WidgetPhotoView`.
  - `backend/channels_app/api.py`: `_message_dict` at :216-225, and a new `ConversationPhotoView` next to the test-chat `post` at :485-519.
  - `backend/channels_app/service/message_service.py:78`: `create_ui_message` gains optional `attachment_info: list | None = None, meta: dict | None = None`.
  - `backend/channels_app/urls_api.py` and the widget URL module.
  - `backend/channels_app/widget_assets/app.js` and `app.css`.
- Test:
  - `backend/channels_app/tests/test_widget.py` (extend)
  - `backend/channels_app/tests/test_test_chat_photo.py`

**Interfaces:**
- Consumes:
  - Task 6 `store_photo`, `NotAnImage`;
  - Task 5 `Widget.image_search_enabled`;
  - Task 1 `Project.image_search_on`;
  - Task 7 meta convention (`photo_reply`).
- Produces:
  - **Widget session:** the session response adds `"image_upload": widget.project.image_search_on and widget.image_search_enabled`.
  - **Visitor messages:** `_visitor_message` adds `"has_photo": bool(msg.has_attachments)`.
  - **`POST <widget messages route>/photo/`:**
    - multipart `image`, optional `text` (≤ `MAX_MESSAGE_CHARS`), `client_id`;
    - same auth and throttles as `WidgetMessagesView.post`;
    - when the image upload flag is false → 403 `photo_disabled`;
    - bad file → 400 `invalid_image`;
    - stores the file with `store_photo(data, f"widget_uploads/{widget.id}")`;
    - creates the inbound message with `has_attachments=True`, `attachment_info=[{"type": "image", "path": <path>}]`, `meta={"photo_reply": True, "client_id"?}` and status `pending`, then calls `schedule_reply`;
    - duplicate `client_id` → returns the existing message (same as text).
  - **`POST api/channels/conversations/<uuid:conversation_id>/photo`** (no trailing slash, like the sibling routes):
    - `IsAuthenticated` plus `_verify_project_access`;
    - another project's conversation → 404;
    - `conversation.channel != "self"` → 400 `not_a_test_conversation`;
    - `not project.image_search_on` → 404 `feature_disabled`;
    - multipart `image`, optional `text`;
    - stores the file with `store_photo(data, f"chat_uploads/{project.slug}")`;
    - `create_ui_message(conversation, text, attachment_info=[...], meta={"photo_reply": True})`, then `schedule_reply`;
    - returns 201 `{"message": _message_dict(msg)}`.
  - **Dashboard messages:** `_message_dict` adds `"has_photo": bool(msg.has_attachments)` and `"photo_matches": (msg.meta or {}).get("photo_matches", "")` for inbound messages, so tenants testing in the dashboard see what was matched.
- **Widget JS:**
  - when `image_upload` is true, show a camera button next to send, with a hidden `<input type="file" accept="image/jpeg,image/png,image/webp">`;
  - a message with `has_photo` renders as `📷 Photo` plus its text;
  - no thumbnail, so the CSP stays unchanged.

- [ ] **Step 1: Write the failing tests**
  - Widget: `test_photo_upload_schedules_reply`, `test_photo_upload_disabled_403` (widget toggle off, and separately tenant off), `test_photo_upload_invalid_image_400`, `test_session_reports_image_upload`.
  - Test chat: `test_test_chat_photo_schedules_reply` (201; message has `photo_reply`; `schedule_reply` patched and called), `test_test_chat_photo_tenant_off_404`, `test_test_chat_photo_other_project_404`, `test_test_chat_photo_non_self_conversation_400`, `test_message_dict_exposes_photo_matches`.
- [ ] **Step 2: Run and confirm they fail.** Run: `docker compose run --rm django python manage.py test channels_app.tests.test_widget channels_app.tests.test_test_chat_photo -v 2`
- [ ] **Step 3: Implement the views, routes, session flag, `create_ui_message` params and the widget JS/CSS**
- [ ] **Step 4: Run and confirm they pass**, along with the full `channels_app` suite.
- [ ] **Step 5: Commit** (`feat(image-search): photo upload in widget and dashboard test chat`)

Add a `ponytail:` note: widget and test-chat uploads are kept forever; add a cleanup beat task if disk use matters.

---

### Task 9: Dashboard (catalog, toggles, test-chat photo, analytics)

**Files:**
- Create: `frontend/components/knowledge/ProductCatalog.tsx`
- Modify:
  - `frontend/lib/api.ts`: add `featuresApi` and `productApi`; add `conversationApi.sendPhoto`; extend the `channelApi`/`widgetApi` types with `image_search_enabled`, the message type with `has_photo`/`photo_matches`, and the analytics type with `photos_indexed`/`photo_searches`.
  - `frontend/app/(dashboard)/knowledge/page.tsx`: render `<ProductCatalog>` in a `CollapsibleCard` titled "Product Catalog" after "Documents" (:1116).
  - `frontend/app/(dashboard)/channels/page.tsx`: per-row button next to the active badge (:479).
  - `frontend/app/(dashboard)/widgets/page.tsx`: per-row button next to Enable/Disable (:137/:224).
  - `frontend/app/(dashboard)/conversations/[id]/page.tsx`: photo button in the test-chat composer.
  - `frontend/app/(dashboard)/analytics/page.tsx`: two stat tiles.
  - `frontend/app/(dashboard)/settings/page.tsx`: "Image search" card.

**Interfaces:**
- Consumes the endpoints from Tasks 1, 4, 5 and 8.
- Produces:
  - `featuresApi.get(projectId): Promise<{ image_search_available: boolean; image_search: boolean }>` and `featuresApi.update(projectId, imageSearch: boolean)` (same return)
  - `productApi.list(projectId, kbId)`, `create(projectId, kbId, name, description, files: File[])` (FormData, raw `fetch` like `documentApi.upload` :187-236), `update(id, {name?, description?})`, `remove(id)`, `addImages(id, files)`, `removeImage(imageId)`
  - `conversationApi.sendPhoto(conversationId, file: File, text?: string)` (FormData)
  - Type `Product = { id; name; description; created_at; images: { id; status: "pending"|"ready"|"failed"; url }[] }`
- UI. Everything below is hidden when `features.image_search` is false:
  - **Catalog form:** name, description, multi-file input (`accept="image/jpeg,image/png,image/webp"`, `multiple`).
  - **Product list:** thumbnails `<img src={API_BASE_URL + image.url}>`, the session cookie authorises them; status badge per image; delete per image and per product; inline edit of name and description.
  - **Polling:** poll the list every 3 s while any image is `pending`.
  - **Channel and widget toggle:** button text `Image search: On` / `Image search: Off` (variant `outline` when off). It calls `channelApi.update(id, {image_search_enabled: !v})` / `widgetApi.update(...)`.
  - **Test chat:** a photo button next to send, shown only on `self` conversations, with the same file `accept`. Inbound messages with `has_photo` show `📷 Photo` plus their text. When `photo_matches` is non-empty, show it under the message in muted small text.
  - **Analytics:** tiles "Photos indexed" and "Photo searches". They are shown regardless of the feature flag, since they read 0 when unused.
  - **Settings page, new "Image search" card** in `frontend/app/(dashboard)/settings/page.tsx`, using the existing `Card` pattern:
    - shown only when `features.image_search_available`;
    - tenant admins get the button `Image search: On` / `Image search: Off`, which calls `featuresApi.update(projectId, !v)`; other roles see the state as text;
    - the helper text reads: "Lets the assistant recognise products from photos customers send. Turn it on per channel too."
    - When not available the card is hidden entirely; the tenant cannot see or request the feature.
  - Native elements and existing `components/ui` only; no new Switch component.

- [ ] **Step 1: Implement `api.ts` additions and `ProductCatalog.tsx`, and wire them into the knowledge page**
- [ ] **Step 2: Add the channel and widget toggle buttons, the test-chat photo button and the analytics tiles**
- [ ] **Step 3: Type-check and lint.** Run: `cd frontend && npx tsc --noEmit && npm run lint`. Expected: no errors.
- [ ] **Step 4: Check end to end in the browser, with `IMAGE_SEARCH_ENABLED=true` in `.env` and the stack up**
  - In Django admin, tick `image_search_allowed` on the test project; the Settings page now shows the card, switched On.
  - Create a product with 2 photos; both become `ready`.
  - In the test chat, send one of those photos: the reply names the product and the match note shows under the message.
  - Toggle the widget on, send a photo from the widget, and confirm the reply names the product.
  - Analytics shows Photos indexed 2 and Photo searches 2.
  - Turn it Off on the Settings page: the catalog card, toggles and photo buttons disappear, and the card stays.
  - Turn it back On, then untick `image_search_allowed` in admin: everything disappears, including the Settings card.
- [ ] **Step 5: Commit** (`feat(dashboard): product catalog, image search toggles, test-chat photos`)

---

## Deliberately left out (add when needed)

- **Sending product photos back to the customer.** Add when tenants ask; this needs per-channel media send.
- **Photo quotas or billing limits.** Usage is metered but not enforced; enforce in `wants_photo_reply` when plans need it.
- **Re-embedding when the CLIP model changes.** Re-queue `embed_product_image` for all `ProductImage` rows.
- **Sending the photo itself to Gemini (vision).** Add if customers ask questions the catalog text can't answer ("does this come in blue?").
