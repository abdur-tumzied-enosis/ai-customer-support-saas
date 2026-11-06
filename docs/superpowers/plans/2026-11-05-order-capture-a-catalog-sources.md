# Order Capture A: Catalog and Product Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Part of the order-capture series; see the index at `docs/superpowers/plans/2026-11-05-order-capture-index.md`.

**Goal:** Products become a real catalog: each has a price, a client key, an availability flag, variants and custom fields, and a status that stops unpriced products from being sold. Products arrive from four sources (KB extraction with pick-to-import, CSV/Excel, Google Sheet, owner edits) under one set of matching and update rules. The catalog works without image search, and the project gets a currency on a new Project settings page.

**Architecture:** Catalog code lives in the `knowledge` app because `Product.kb` already anchors products there. Pure logic (price parsing, status, matching, row rules) sits in plain modules with no request code, so it is tested first. Every import path (CSV, Sheet, KB candidates) ends in the same row-apply function, so the rules cannot drift. Image search stays gated and applies only to photo embedding and photo search. Catalog endpoints do not check it.

**Tech Stack:** Django 5 / DRF, Celery, pydantic (as in `channels_app/memory.py`), `openpyxl` for `.xlsx`, `requests`, Next.js (TypeScript), Tailwind + the existing UI kit in `frontend/components/ui/`.

**Spec:** `docs/superpowers/specs/2026-11-04-order-capture-design.md` (Parts 0, 1 and 2; the A rows of Part 8). Read it before starting.

## Global Constraints

- Project currency: `BDT` by default, shown as ৳ wherever prices appear. Set on the Project settings page.
- `active` requires a non-null price. Enforced as a DB CHECK constraint, not only in the UI.
- Matching order for every source: `external_id`, then `sku`, then normalised name plus variant attributes. Name matches carry `matched_by = "name"` so the UI can mark them.
- Owner always wins: fields in `locked_fields` are never changed by a sync.
- A product removed from a source is set to `hidden`, never deleted.
- CSV and Sheet changes apply automatically. Changes from KB extraction go to review.
- A price that cannot be parsed leaves the price empty, so the product becomes `needs_info`.
- Nothing becomes a product from KB extraction until the client picks it.
- Images from import are downloaded whether or not image search is on. Embedding runs only when image search is on.
- Key warning, verbatim: "Add your product ID or SKU so updates match the right product. Without it we match by name, which can mix up similar products."
- A duplicate `external_id` inside one import rejects that row with a message.
- Member role: view and edit products. Admin role: product fields, sources, imports, Project settings.
- Response envelope: `{"success": true, "data": ...}` or `{"success": false, "error": {"code", "message"}}`.
- Token usage: KB extraction records `source: "product_extract"` on `chat_in` and `chat_out`, and product embedding keeps its existing `embed` record. CSV column mapping is heuristic only; no LLM call.
- Backend tests: `docker compose run --rm django python manage.py test <dotted.module> -v 2` from the repo root.
- Frontend checks: `cd frontend && npx tsc --noEmit && npm run lint`. The frontend has no test runner, so UI tasks are verified by these two plus a manual pass in the dev server.

## Review Focus

1. **An unpriced product becomes active by any path** (PATCH, CSV, Sheet, candidate import, direct write). Tests: `test_db_rejects_active_without_price` (Task A2), `test_patch_active_without_price_rejected` (A5), `test_csv_row_without_price_is_needs_info` (A11), `test_candidate_import_without_price_is_needs_info` (A9).
2. **A sync overwrites a field the owner edited by hand.** Test: `test_owner_locked_price_kept_on_sync` (A11).
3. **A crawl's junk page becomes products without the client picking it.** Test: `test_extraction_creates_candidates_only` (A8).
4. **Two rows with the same `external_id` in one import.** Test: `test_duplicate_external_id_row_rejected` (A11).
5. **Photo search returns an unpriced or hidden product** the agent could then sell. Test: `test_image_search_excludes_non_active` (A16).

---

### Task A1: Project currency and the Project settings page

**Files:**
- Modify: `backend/accounts/models.py` (class `Project`, lines 22-41)
- Create: migration under `backend/accounts/migrations/`
- Modify: `backend/accounts/views.py`, `backend/accounts/urls.py`
- Create: `backend/accounts/tests/test_project_settings.py`
- Create: `frontend/app/(dashboard)/project-settings/page.tsx`, `frontend/lib/currencies.ts`
- Modify: `frontend/app/(dashboard)/settings/page.tsx` (remove the Image search card at lines 211-236, its toggle at 60-71 and its fetch at 52-58)
- Modify: `frontend/app/(dashboard)/layout.tsx` (avatar menu, lines 95-100: add a "Project settings" link)
- Modify: `frontend/lib/api.ts` (add `projectSettingsApi`)

**Interfaces:**
- Produces: `Project.currency` = `CharField(max_length=3, default="BDT")`.
- Produces: `GET` and `PATCH /api/projects/settings/?project_id=`. Response data: `{"project_id", "name", "currency"}`. PATCH accepts `name` and `currency`. Currency must be three uppercase ASCII letters, else 400 `invalid_currency`. PATCH needs admin, else 403 `access_denied`.

- [ ] **Step 1: Write the failing tests** in `test_project_settings.py`:
  - `test_default_currency_is_bdt`: a new project reports `currency == "BDT"`.
  - `test_patch_currency_saves`: PATCH `{"currency": "USD"}` returns 200 and persists.
  - `test_invalid_currency_rejected`: `"bd1"` returns 400, `error.code == "invalid_currency"`.
  - `test_member_cannot_patch`: a member gets 403.

- [ ] **Step 2: Run to verify failure.** `docker compose run --rm django python manage.py test accounts.tests.test_project_settings -v 2` fails because the field and route are missing.

- [ ] **Step 3: Implement.** Add the field, run `makemigrations accounts`, add the view and route beside the existing project routes in `accounts/urls.py`. Validation is a regex `^[A-Z]{3}$` in the serializer.

- [ ] **Step 4: Run tests to verify they pass.** Same command, all four green.

- [ ] **Step 5: Frontend.** `lib/currencies.ts` exports `CURRENCIES: {code, symbol, name}[]` with BDT first (৳) and about fifteen common codes (USD $, EUR €, GBP £, INR ₹, AED, SAR, MYR, SGD, and so on). The page at `project-settings/page.tsx` has a project name field and a searchable currency select (a filter `Input` over a list of buttons, since the UI kit has no combobox). Move the Image search card here, reusing the same `featuresApi` calls. Remove the card and its code from `settings/page.tsx`. Add the avatar menu link.

- [ ] **Step 6: Verify.** `cd frontend && npx tsc --noEmit && npm run lint`. Both clean.

- [ ] **Step 7: Commit.** `feat(settings): project settings page with currency`

---

### Task A2: Product model, variants, custom fields, status constraint

**Files:**
- Modify: `backend/knowledge/models.py` (class `Product`, line 217)
- Add classes in the same file: `ProductVariant`, `ProductField`, `ProductSource`
- Create: migration under `backend/knowledge/migrations/`
- Create: `backend/knowledge/tests/test_catalog_models.py`

**Interfaces:**
- Produces `Product` new fields: `external_id` (CharField 255, null, blank), `sku` (CharField 128, blank), `price` (DecimalField 12,2, null, blank), `available` (Bool, default True), `status` (CharField 16, choices `needs_info` / `active` / `hidden`, default `needs_info`), `product_url` (TextField, blank), `source` (CharField 16, choices `manual` / `kb` / `csv` / `sheet`, default `manual`), `source_ref` (FK `ProductSource`, SET_NULL, null), `custom` (JSONField, default dict), `locked_fields` (JSONField, default list), `last_seen_at` (DateTime, null).
- Produces `ProductVariant`: `product` FK (related `variants`, CASCADE), `external_id` (null), `sku` (blank), `label` (255), `attributes` (JSON dict), `price` (Decimal, null), `available` (Bool True).
- Produces `ProductField`: `kb` FK (related `product_fields`, CASCADE), `key` (SlugField 64), `label` (255), `kind` (`text` / `number` / `choice`), `choices` (JSON list), `required` (Bool), `show_to_agent` (Bool), `position` (Int). Unique `(kb, key)`.
- Produces `ProductSource`: `kb` FK (related `product_sources`, CASCADE), `kind` (`csv` / `sheet`), `mapping` (JSON dict), `sheet_url` (URLField, blank), `last_synced_at` (null), `last_error` (TextField, blank), `created_at`.
- Constraints on `Product`:
  - `product_active_requires_price`: `CheckConstraint(check=~Q(status="active") | Q(price__isnull=False))`.
  - `product_external_id_unique_per_kb`: `UniqueConstraint(fields=["kb", "external_id"], condition=Q(external_id__isnull=False))`.

- [ ] **Step 1: Write the failing tests**:
  - `test_db_rejects_active_without_price`: inside `transaction.atomic()`, `Product.objects.create(kb=kb, name="x", status="active", price=None)` raises `IntegrityError`.
  - `test_external_id_unique_per_kb_when_set`: two products with `external_id="A1"` in one KB raise `IntegrityError`.
  - `test_null_external_id_not_unique`: two products with `external_id=None` both save.
  - `test_same_external_id_allowed_in_other_kb`.
  - `test_default_status_needs_info`.

- [ ] **Step 2: Run to verify failure.** `docker compose run --rm django python manage.py test knowledge.tests.test_catalog_models -v 2`.

- [ ] **Step 3: Implement** the fields, classes and constraints. Existing rows pick up the `needs_info` default in the AddField migration, so no data migration is needed. Every existing product has no price, so all of them correctly show as needing info.

- [ ] **Step 4: Run tests to verify they pass.** Then `manage.py migrate` on the local DB.

- [ ] **Step 5: Commit.** `feat(catalog): price, keys, variants and custom fields on products`

---

### Task A3: Price parsing

**Files:**
- Create: `backend/knowledge/price.py`
- Create: `backend/knowledge/tests/test_price.py`

**Interfaces:**
- Produces `parse_price(raw: str | None) -> Decimal | None`. Returns `None` for empty, unparseable, zero or negative results.

- [ ] **Step 1: Write the failing test** as a table of cases:

```python
import unittest
from decimal import Decimal
from knowledge.price import parse_price

CASES = [
    ("৳1,200", "1200"),
    ("1200/-", "1200"),
    ("Tk 1200", "1200"),
    ("BDT 1200", "1200"),
    ("১২০০", "1200"),
    ("৳১,২০০", "1200"),
    ("1,200.50", "1200.50"),
    ("৳1,200 (regular ৳1,500)", "1200"),       # regular price excluded
    ("Regular ৳1,500, now ৳1,200", "1200"),    # sale after regular
    ("", None),
    (None, None),
    ("call for price", None),
    ("0", None),
]

class ParsePriceTest(unittest.TestCase):
    def test_cases(self):
        for raw, want in CASES:
            with self.subTest(raw=raw):
                got = parse_price(raw)
                self.assertEqual(got, Decimal(want) if want else None)
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Translate Bengali digits (U+09E6 to U+09EF) to ASCII. Strip `৳`, `Tk`, `BDT`, `টাকা` and `/-`. Find amounts with `\d[\d,]*(?:\.\d{1,2})?`. Drop an amount if the 20 characters before it contain `regular`, `reg.`, `was`, `original` or `মূল্য`. Return the first remaining amount as Decimal, or `None` if none remain or the value is not positive. Add the comment `# ponytail: keyword heuristic for sale vs regular, not a grammar; upgrade if a real feed breaks it`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): parse BDT price text`

---

### Task A4: Catalog rules (status, keys, matching, owner edits)

**Files:**
- Create: `backend/knowledge/catalog.py`
- Create: `backend/knowledge/tests/test_catalog.py`

**Interfaces:**
- `normalise_name(text: str) -> str`: NFKC, casefold, Bengali digits to ASCII, punctuation to spaces, collapse whitespace.
- `effective_price(product, variant=None) -> Decimal | None`: `variant.price` if set, else `product.price`.
- `compute_status(product, fields: list[ProductField]) -> str`:
  - `hidden` stays `hidden`.
  - No product price gives `needs_info`.
  - A required field missing from `product.custom` gives `needs_info`.
  - An available variant whose effective price is None gives `needs_info`.
  - Otherwise `active`.
- `sellable(product) -> bool`: `status == "active" and available`.
- `product_key(product) -> str`: `external_id`, else `sku`, else `""`.
- `match_product(kb, external_id, sku, name, attributes: dict) -> tuple[Product | None, str]`: tries external_id, then sku, then normalised name plus sorted normalised attributes. Returns `matched_by` as `"external_id"`, `"sku"`, `"name"` or `""`.
- `apply_owner_edit(product, changes: dict, fields) -> None`: sets each changed field, adds each key to `locked_fields`, and recomputes `status` unless `status` itself was set by the owner. Set `status` only by owner choice.

- [ ] **Step 1: Write the failing tests**: `test_compute_status_no_price_needs_info`, `test_compute_status_required_custom_missing`, `test_compute_status_all_ok_active`, `test_hidden_is_kept`, `test_normalise_matches_bengali_digits` (`normalise_name("Saree ৪৫০") == normalise_name("saree 450")`), `test_match_external_id_beats_sku` (two products, one matched by sku and one by external_id, external_id wins), `test_name_match_reports_matched_by_name`, `test_apply_owner_edit_locks_price`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** the functions above. Keep `status` recomputation in one place, `compute_status`, and call it from every write path.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): status, keys, matching and owner locks`

---

### Task A5: Catalog endpoints, separated from image search

**Files:**
- Modify: `backend/knowledge/products.py` (`_gate` at line 54; `ProductListCreateView` at 132; `ProductDetailView` at 177)
- Create: `backend/knowledge/catalog_views.py` (new endpoints in later tasks)
- Modify: `backend/knowledge/urls.py`
- Create: `backend/knowledge/tests/test_catalog_api.py`

**Interfaces:**
- Add `_catalog_gate(request, project, admin: bool)`: membership and role checks only. `_gate` (image search) stays and is used only by the photo views (`ProductImagesAddView`, `ProductImageDetailView`, `ProductImagesConfirmView`, `ProductImageFileView`) and `FeaturesView` keeps its own check.
- `catalog.product_dict(product) -> dict`: `id`, `kb_id`, `external_id`, `sku`, `name`, `description`, `price` (string or null), `currency` (from project), `available`, `status`, `product_url`, `source`, `custom`, `locked_fields`, `variants` (list), `images` (existing shape from `_product_dict`), `created_at`.
- `GET /api/kb/products/summary/?project_id=&kb_id=` returns `{"counts": {"needs_info", "active", "hidden"}, "currency"}`.
- `POST /api/kb/products/` takes `name` (required), `description`, `price`, `sku`, `external_id`, `available`, `custom`, `product_url`. The response includes `key_warning: true` when both `external_id` and `sku` are empty. Member role.
- `PATCH /api/kb/products/<id>/` routes through `apply_owner_edit`. Requesting `status: "active"` without a price returns 400 `price_required`. Member role.
- Photo endpoints with image search off return 404 `feature_disabled`, as today.

- [ ] **Step 1: Write the failing tests** in `test_catalog_api.py`, with `IMAGE_SEARCH_ENABLED=False` via `override_settings`:
  - `test_catalog_crud_works_with_image_search_off`: create returns 200 with `status == "needs_info"`.
  - `test_patch_price_makes_active_and_locks_price`.
  - `test_patch_active_without_price_rejected`: 400, `error.code == "price_required"`.
  - `test_photo_endpoints_still_gated`: POST `images/` returns 404 `feature_disabled`.
  - `test_summary_counts`.
  - `test_key_warning_when_no_key`.

- [ ] **Step 2: Run to verify failure.** `docker compose run --rm django python manage.py test knowledge.tests.test_catalog_api -v 2`.

- [ ] **Step 3: Implement.** Switch the catalog views to `_catalog_gate`. Route the write path through `compute_status` and `apply_owner_edit`. Make `summary` a function view in `catalog_views.py`.

- [ ] **Step 4: Run tests to verify they pass.** Then re-run `knowledge.tests.test_product_api` and `knowledge.tests.test_products` to confirm the photo behaviour is unchanged.

- [ ] **Step 5: Commit.** `feat(catalog): catalog endpoints no longer require image search`

---

### Task A6: Custom product fields per KB

**Files:**
- Modify: `backend/knowledge/catalog_views.py`, `backend/knowledge/urls.py`
- Create: `backend/knowledge/tests/test_product_fields.py`

**Interfaces:**
- `GET`, `POST /api/kb/product-fields/?kb_id=`. `PATCH`, `DELETE /api/kb/product-fields/<id>/`. Admin role.
- Creating `kind: "choice"` requires non-empty `choices`.
- Changing `required` or deleting a field recomputes `status` for every product in that KB. Deleting a field leaves values in `Product.custom`.

- [ ] **Step 1: Write the failing tests**: `test_required_field_makes_product_needs_info`, `test_adding_required_field_recomputes_status`, `test_key_unique_per_kb` (duplicate key returns 400 `duplicate_key`), `test_choice_requires_choices` (400 `choices_required`), `test_delete_keeps_custom_values`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Use `compute_status` for the recompute, in one loop per KB.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): per-KB custom product fields`

---

### Task A7: Variants

**Files:**
- Modify: `backend/knowledge/catalog.py` (add `save_variants(product, items: list[dict]) -> None`)
- Modify: `backend/knowledge/catalog_views.py` (accept `variants` in the product PATCH and POST)
- Create: `backend/knowledge/tests/test_variants.py`

**Interfaces:**
- `save_variants(product, items)`: replaces the variant set. Each item is `{external_id?, sku?, label, attributes?, price?, available?}`. A variant with no price falls back to the product price.

- [ ] **Step 1: Write the failing tests**: `test_variant_without_price_uses_product_price` (product active, variant price null, `effective_price` returns product price), `test_available_variant_without_any_price_needs_info` (product price null, variant price null gives `needs_info`), `test_unavailable_variant_ignored_for_status`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** `save_variants`. Have `compute_status` read variants. Attach `variants` to `product_dict`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): product variants with price fallback`

---

### Task A8: KB product extraction into candidates

**Files:**
- Modify: `backend/knowledge/models.py` (add `ProductCandidate`, `IgnoredProductPage`)
- Create: `backend/knowledge/product_extract.py`
- Modify: `backend/knowledge/tasks.py` (add `extract_product_candidates`)
- Create: `backend/knowledge/tests/test_product_extract.py`
- Create: migration

**Interfaces:**
- `ProductCandidate`: `kb` FK, `document` FK `Document` (SET_NULL, null), `page_url` (TextField), `name` (255), `payload` (JSON dict: `description`, `external_id`, `sku`, `price` as string or null, `variants`, `image_srcs`, `custom`), `confidence` (Float), `status` (`pending` / `imported` / `ignored`, default `pending`), `matched_product` FK `Product` (SET_NULL, null), `changes` (JSON dict, default `{}`), `created_at`.
- `IgnoredProductPage`: `kb` FK, `url` (CharField 1000), unique `(kb, url)`.
- `extract_product_candidates(kb_id: str, document_ids: list[str] | None = None) -> int` as a `shared_task`. Returns the number of candidates created.
- Model call: follow `channels_app/memory.py` `ask_llm` (lines 45-61). Use `get_llm` with the same role that `is_page_relevant` uses (`knowledge/tasks.py:190`), `with_structured_output(schema, include_raw=True, method="function_calling")`, and `llm_costs` plus `track_token_usage` with `metadata = {"source": "product_extract", "kb_id", "document_id", "model"}`.
- Limits (Claude's call): `MAX_PAGES_PER_RUN = 50`, `MAX_CHARS_PER_PAGE = 24000`, `MAX_CANDIDATES_PER_PAGE = 30`.

- [ ] **Step 1: Write the failing tests**. Patch `knowledge.product_extract.get_llm` to return a fake structured result:
  - `test_extraction_creates_candidates_only`: `Product.objects.count()` is unchanged after extraction.
  - `test_ignored_page_skipped`: a page in `IgnoredProductPage` produces no candidates and no LLM call.
  - `test_image_src_not_on_page_dropped`: a returned `image_srcs` entry absent from `document.images_metadata[*].src` is removed.
  - `test_unchanged_match_not_listed`: a match with equal price creates no candidate.
  - `test_price_change_recorded`: a match with a new price stores `changes == {"price": {"old": "450", "new": "500"}}`.
  - `test_locked_field_not_in_changes`: a locked price produces no `price` entry.
  - `test_tokens_tracked_with_source`: `track_token_usage` is called with `metadata["source"] == "product_extract"`.
  - `test_price_text_parsed_server_side`: the candidate's price comes from `parse_price(price_text)`, never from the LLM number.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Store candidates; never call `Product.objects.create` in this module. Add `confidence` clamping to 0 to 1.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): KB product extraction into review candidates`

---

### Task A9: Candidate review API and import

**Files:**
- Modify: `backend/knowledge/catalog_views.py`, `backend/knowledge/urls.py`
- Create: `backend/knowledge/tests/test_candidates_api.py`

**Interfaces:**
- `GET /api/kb/products/candidates/?kb_id=&status=pending` returns `{"pages": [{"page_url", "page_title", "candidates": [...]}], "ignored_pages": [...]}`, grouped by `page_url`. Each candidate has `low_confidence` (`confidence < 0.6`).
- `POST /api/kb/products/candidates/import/` with `{"ids": [...]}` returns `{"imported", "updated", "rejected": [{"id", "message"}]}`. For an unmatched candidate it creates a product with `source="kb"`, `product_url=page_url`, `price=parse_price(...)`, and status from `compute_status`. It enqueues `fetch_product_images` (A10) on commit. For a matched candidate it applies `changes` without touching locked fields. Duplicate `external_id` in the same batch is rejected with a message.
- `POST /api/kb/products/candidates/ignore-page/` with `{"kb_id", "page_url"}` records `IgnoredProductPage` and marks that page's pending candidates `ignored`.
- `POST /api/kb/products/extract/` with `{"kb_id"}` enqueues `extract_product_candidates` and returns 202 `{"queued": true}`. Admin role.

- [ ] **Step 1: Write the failing tests**: `test_import_only_picked_candidates`, `test_import_without_price_is_needs_info` (`test_candidate_import_without_price_is_needs_info`), `test_import_saves_page_url`, `test_ignore_page_hides_candidates_and_remembers`, `test_duplicate_external_id_row_rejected` (two picked candidates with one external_id: the second is rejected with a message, and the first is imported).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Import runs in one `transaction.atomic()` per request. Each row's outcome is independent, so one rejection does not cancel the others.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): review and import KB product candidates`

---

### Task A10: Image download on import

**Files:**
- Create: `backend/knowledge/image_fetch.py`
- Modify: `backend/knowledge/tasks.py` (add `fetch_product_images(product_id: str, image_srcs: list[str])`)
- Create: `backend/knowledge/tests/test_image_fetch.py`

**Interfaces:**
- `fetch_image(url: str) -> tuple[bytes, str]` returns bytes and extension. Raises `ImageFetchError`.
- `attach_product_images(product, urls: list[str]) -> None`: creates `ProductImage` rows (`status="pending"`), then `ready` or `failed` with `error` text. Stores through `product_image_path(kb, image_id, ext)` and `default_storage`.
- Safety: `_is_public_host` and `_parse_target` from `agents/api_tool.py`, as `channels_app/webhooks.py` already imports them. `requests.get` with `allow_redirects=False`, `timeout=10`, a byte cap of `MAX_PRODUCT_IMAGE_BYTES`. Allowed types: `image/jpeg`, `image/png`, `image/webp`. At most `MAX_IMAGES_PER_PRODUCT` images.
- After a successful image, if `project.image_search_on`, enqueue `embed_product_image` on commit. Otherwise skip.

- [ ] **Step 1: Write the failing tests**. Patch `knowledge.image_fetch.requests.get` and `agents.api_tool.socket.getaddrinfo`:
  - `test_fetch_blocks_internal_url`: `http://127.0.0.1/a.jpg` and `http://169.254.169.254/` raise `ImageFetchError`, and `requests.get` is not called.
  - `test_redirect_not_followed`: a 302 is an error, and `allow_redirects` is `False`.
  - `test_oversize_marks_failed`.
  - `test_bad_type_marks_failed`.
  - `test_product_kept_when_image_fails`: the product still exists and the `ProductImage` is `failed`.
  - `test_embed_only_when_image_search_on`: `embed_product_image` is enqueued only with image search on.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Use the same `_parse_target` and host check as `agents/api_tool.py:42-60`. Keep one private helper per concern.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): download product images on import`

---

### Task A11: CSV and Excel source, and the shared row rules

**Files:**
- Create: `backend/knowledge/product_sources.py` (`parse_table`, `guess_mapping`, `apply_rows`)
- Modify: `backend/knowledge/catalog.py` (add `SourceRow`, `apply_source_row`, `notify_owners_price_cleared`)
- Modify: `backend/knowledge/catalog_views.py` (`POST /api/kb/products/sources/csv/preview/`, `POST /api/kb/products/sources/csv/`, `GET /api/kb/products/sources/?kb_id=`)
- Modify: `backend/requirements.txt` (add `openpyxl` if not present; pin the current stable version)
- Create: `backend/knowledge/tests/test_product_sources.py`

**Interfaces:**
- `parse_table(filename: str, data: bytes) -> tuple[list[str], list[dict[str, str]]]`. CSV is read with `utf-8-sig`. `.xlsx` uses the first sheet via openpyxl. The first row is the header.
- `guess_mapping(headers: list[str]) -> dict[str, str | None]`. Targets: `external_id`, `sku`, `name`, `price`, `description`, `available`, `variant_label`, `product_url`, and `custom:<key>`. Heuristics on lowercased tokens, including Bengali terms (`দাম`, `মূল্য`).
- `SourceRow` dataclass: `external_id`, `sku`, `name`, `price_text`, `available`, `description`, `product_url`, `custom`, `variant_label`, `attributes`.
- `apply_source_row(kb, source, row: SourceRow, sync_started_at) -> str`. Returns `created`, `updated`, `unchanged` or `rejected`. Rules:
  - A match by key or name updates `price` and `available` unless locked. Name and description update unless locked.
  - A price that parses to `None` sets `price` to None. A product that had a price and loses it is sent to `needs_info` and `notify_owners_price_cleared(product)` is called, unless `price` is locked.
  - Status is recomputed with `compute_status`. `source_ref` and `last_seen_at = sync_started_at` are set.
- `apply_rows(kb, source, rows: list[dict], mapping, sync_started_at) -> dict`. Returns `{"created", "updated", "unchanged", "hidden", "rejected": [{"row", "message"}]}`. Products from this source whose `last_seen_at < sync_started_at` are set to `hidden`, unless `status` is locked.
- `notify_owners_price_cleared(product)`: `queue_email` (from `accounts/tasks.py`) to each admin of the project, with product name and a link.
- Preview response: `{"headers", "mapping", "sample": first 5 rows, "key_warning": bool}`. `key_warning` is true when the mapping has neither `external_id` nor `sku`.
- Creating the source saves `ProductSource(kind="csv", mapping=...)` and runs `apply_rows` once. Admin role.

- [ ] **Step 1: Write the failing tests**:
  - `test_guess_mapping_bengali_headers`: headers `["পণ্য কোড", "নাম", "দাম"]` map to `external_id`, `name`, `price`.
  - `test_csv_creates_active_with_price`.
  - `test_csv_row_without_price_is_needs_info`.
  - `test_csv_clear_price_sends_to_needs_info_and_notifies`: patch `knowledge.catalog.queue_email`, assert it is called once.
  - `test_owner_locked_price_kept_on_sync`: lock `price`, sync a different price, value unchanged.
  - `test_removed_row_hides_product`.
  - `test_duplicate_external_id_row_rejected`: second row gets a `rejected` entry with a message.
  - `test_key_warning_when_no_key_column`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** the functions above. Keep `apply_source_row` as the only place these rules are written. The Sheet and candidate paths call it too.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): CSV and Excel product source with shared sync rules`

---

### Task A12: Google Sheet source and scheduled sync

**Files:**
- Modify: `backend/knowledge/product_sources.py` (`sheet_export_url`, `fetch_sheet_csv`)
- Modify: `backend/knowledge/tasks.py` (`sync_product_source(source_id)`, `sync_product_sources()`)
- Modify: `backend/knowledge/catalog_views.py` (`POST /api/kb/products/sources/sheet/`, `POST /api/kb/products/sources/<id>/sync/`, `DELETE /api/kb/products/sources/<id>/`)
- Modify: `backend/core/settings.py` (`CELERY_BEAT_SCHEDULE`, around lines 129-150): add `"sync-product-sources"` with `task: "knowledge.tasks.sync_product_sources"`, `schedule: 3600`
- Create: `backend/knowledge/tests/test_sheet_source.py`

**Interfaces:**
- `sheet_export_url(sheet_url: str) -> str`: parses the spreadsheet id and optional `gid` and returns `https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>`. The host is fixed to `docs.google.com`, which is the SSRF guard. Any other host raises `ValueError`.
- `fetch_sheet_csv(url: str) -> bytes`: raises `SheetNotPublic` on a non-200 response or an HTML body. Message: "Share the sheet as 'Anyone with the link can view'."
- Failure is saved to `ProductSource.last_error` with code `sheet_not_public`. The mapping is reused on every sync.
- `DELETE` on a source keeps its products and sets their `source_ref` to None. Products are not hidden.

- [ ] **Step 1: Write the failing tests**: `test_sheet_export_url_parses_id_and_gid`, `test_other_host_rejected`, `test_sheet_not_public_error` (HTML response gives the error and `last_error` is saved), `test_sync_uses_saved_mapping`, `test_sync_now_endpoint`, `test_beat_schedule_has_sync` (the setting key exists).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Use `requests.get` with `timeout=15` and `allow_redirects=False`. Treat a redirect to a login page as not public.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(catalog): Google Sheet product source with scheduled sync`

---

### Task A13: Products frontend (catalog table, inline price, needs-info)

**Files:**
- Modify: `frontend/components/knowledge/sections/ProductsSection.tsx` (rewrite as the catalog)
- Create: `frontend/components/knowledge/catalog/ProductTable.tsx`, `ProductEditor.tsx` (fields and variants), `NeedsInfoBanner.tsx`
- Modify: `frontend/lib/api.ts` (`productApi`: add `summary`, `list` with a status filter, `patch` with price; add `productFieldApi`)
- Modify: `frontend/components/knowledge/KnowledgeSectionNav.tsx` (show the needs-info count on the Products entry)
- Modify: `frontend/components/layout/Sidebar.tsx` (count badge on Knowledge, reusing the existing conversation badge pattern at lines 38-48 and 129-139)

**Behaviour:**
- Banner: "N products need a price".
- Status tabs: All, Needs info, Active, Hidden.
- Inline price: type, Enter saves, focus moves to the next `needs_info` row.
- Key warning shown with the exact text from the Global Constraints.
- The photo UI (`ProductCatalog.tsx`) appears only when `imageSearchOn` (`KnowledgeContext.tsx:90`), inside the product editor.
- The Products section no longer depends on image search for visibility.

- [ ] **Step 1: Implement** the components and API client functions.

- [ ] **Step 2: Verify.** `cd frontend && npx tsc --noEmit && npm run lint`. Then in the dev server, check the table, banner, Enter-to-next and the tabs by hand. Say in the report if this was not run.

- [ ] **Step 3: Commit.** `feat(frontend): product catalog with needs-info prompts`

---

### Task A14: Sources and candidate review frontend

**Files:**
- Create: `frontend/components/knowledge/catalog/ProductSourcesPanel.tsx` (CSV upload with a mapping confirm step, Google Sheet link with the not-public error, Sync now, list of sources)
- Create: `frontend/components/knowledge/catalog/CandidateReview.tsx` (grouped by page; "Select all from this page"; "Ignore this page next time"; low-confidence highlight; thumbnails from the candidate's image URLs; "Re-run extraction"; changes shown as "old → new")
- Modify: `frontend/components/knowledge/sections/ProductsSection.tsx` (tabs: Products, Sources, Review (N))
- Modify: `frontend/lib/api.ts` (candidate, source and extract functions)

- [ ] **Step 1: Implement.** Show the key warning on the sources panel when the preview says `key_warning`.

- [ ] **Step 2: Verify.** `npx tsc --noEmit && npm run lint`. Manual pass in the dev server.

- [ ] **Step 3: Commit.** `feat(frontend): product sources and candidate review`

---

### Task A15: Token tracking check

**Files:**
- Create: `backend/knowledge/tests/test_catalog_usage.py`
- Modify (only if a gap is found): `backend/knowledge/tasks.py` (`embed_product_image`)

- [ ] **Step 1: Write the tests.** `test_extraction_tracks_chat_in_and_out` (both kinds recorded, source `product_extract`). `test_product_embed_recorded` (embedding a product image records usage with source `product_embed` or the existing `index` op, whichever the code already uses. Check the existing call first and document which one it is).

- [ ] **Step 2: Run to verify.** Add the missing `track_token_usage` call if a test fails.

- [ ] **Step 3: Run the whole knowledge and accounts suites.** `docker compose run --rm django python manage.py test knowledge accounts -v 2`.

- [ ] **Step 4: Commit.** `test(catalog): token usage for product extraction`

---

### Task A16: Photo search returns only active products

**Files:**
- Locate first: `grep -n "Product" backend/channels_app/image_reply.py backend/agents/image_search.py backend/knowledge/products.py` to find where product photo matches are resolved to `Product` rows.
- Modify: that lookup, adding `status="active"`.
- Create: `backend/knowledge/tests/test_image_search_catalog.py`

- [ ] **Step 1: Write the failing test** `test_image_search_excludes_non_active`: a `hidden` product and a `needs_info` product with a matching photo are not returned, and an `active` one is.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** the filter at the lookup found above. Do not change the Qdrant payloads.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `fix(image-search): only return active products`

---

## Done when

- Every Review Focus test passes.
- `docker compose run --rm django python manage.py test knowledge accounts -v 2` is green.
- `cd frontend && npx tsc --noEmit && npm run lint` is clean.
- A product with no price cannot be `active` through any path.
