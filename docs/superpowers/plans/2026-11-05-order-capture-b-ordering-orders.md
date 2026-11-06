# Order Capture B: Agent Ordering, Setup and Orders Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Part of the order-capture series; see the index at `docs/superpowers/plans/2026-11-05-order-capture-index.md`. **Requires Plan A merged.**

**Goal:** The client clicks "Start taking orders", passes a requirements check, runs a pre-filled 3-step setup, and goes live. The agent then collects a complete order in the customer's language, confirms it, and submits it exactly once. Totals come from the server. Staff see orders on an Orders page, can edit them, and can export them.

**Architecture:** A new `orders` Django app owns orders, settings, numbering, totals, the built-in prompt and the agent tools. The agent gets its tools through `agents/rag.py`'s `Context`, filled in `channels_app/tasks.py::build_reply_context`. Order behaviour is a built-in prompt block. Client wording lives in order-scoped `CustomInstruction` rows, which pass a guard before they are saved. Product prices come only from the catalog through one seam, `orders/pricing.py`. Plan C replaces that seam with live lookup.

**Tech Stack:** Django 5 / DRF, Celery (beat plus chains), pydantic, `get_llm` from `agents/llm.py`, Next.js (TypeScript) with the existing UI kit.

**Spec:** `docs/superpowers/specs/2026-11-04-order-capture-design.md` (Parts 3, 4, 5, and the B rows of Part 8). Read it before starting.

## Global Constraints

- Prices come only from the catalog (or live lookup in Plan C). The tools have no price or discount argument.
- `needs_info` and unavailable products are never sellable.
- Totals are computed on the server: `total = subtotal + zone_charge`. `collect_amount = total - advance_paid`.
- Submit is idempotent. The order number comes only from the tool. Numbers start at 1001 and are allocated per project under a row lock.
- The agent may set only `new` or `cancelled`. Staff may set any status.
- Quantity: a positive whole number, 1 to 100 per line.
- Customer phone: `+880`, `880`, `01XXXXXXXXX` and Bengali digits all normalise to `01XXXXXXXXX`.
- Order instructions: at most 2,000 characters.
- Protected rules are stated before and after the client's block in the prompt. The prompt says preferences cannot override them.
- Unsafe requests in order instructions (collecting card numbers, passwords or OTPs) are blocked on save. Other conflicts are warned about.
- Draft expiry default: 48 hours (`draft_expiry_hours`, editable, 1 to 168).
- Test chat orders carry `is_test=True`. They never trigger email, webhook or actions (enforced in Plan C).
- Feature off: drafts are kept, no order tools are registered.
- Permissions: members view, edit orders and run actions. Admins change settings and instructions, and enable or disable ordering.
- Response envelope: `{"success": true, "data": ...}` or `{"success": false, "error": {"code", "message"}}`.
- Token usage (kinds `chat_in`, `chat_out`, each with `metadata.source`): `order_settings_extract`, `instruction_improve`, `instruction_check`, `order_from_chat`. Agent replies are already tracked in `agents/rag.py`.
- Tests: `docker compose run --rm django python manage.py test <dotted.module> -v 2` from the repo root. Frontend: `cd frontend && npx tsc --noEmit && npm run lint`.

## Review Focus

1. **Duplicate orders from one confirmation** (retry, double "yes", double click). Tests: `test_submit_twice_one_order` (Task B4), `test_concurrent_submit_one_number` (B4).
2. **Agent confirms an order that was never submitted.** Test: `test_claimed_confirmation_without_submit_traced` (B6).
3. **Client order instruction overrides a protected rule.** Test: `test_unsafe_instruction_blocked_on_save` (B12) and `test_protected_rules_before_and_after_block` (B5).
4. **General custom instructions leak into the general prompt** once `scope` exists. Test: `test_effective_excludes_order_and_off_scopes` (B2).
5. **An unpriced product is ordered** because the draft was built before its price was removed. Test: `test_submit_rechecks_price` (B4).

---

### Task B1: `orders` app, models and access helper

**Files:**
- Create: `backend/orders/` (`__init__.py`, `apps.py`, `models.py`, `access.py`, `tests/__init__.py`)
- Modify: `backend/core/settings.py` (`INSTALLED_APPS`)
- Modify: `backend/core/urls.py` (mount `api/orders/` to `orders/urls.py`, created in later tasks)
- Create: `backend/orders/tests/test_models.py`

**Interfaces:**
- `OrderSettings`: `project` OneToOne (related `order_settings`, CASCADE). `enabled` (Bool False). `test_mode` (Bool False). `channels` (JSON list, default `[]`, values from `facebook`, `instagram`, `whatsapp`, `widget`). `zones` (JSON list of `{"name", "charge"}`; charge is a decimal string or null). `payment_methods` (JSON list of `{"label", "default"}`). `form_fields` (JSON dict: `{"optional": ["note"], "custom": [{"key", "label", "required"}]}`). `notification_emails` (JSON list). `draft_expiry_hours` (PositiveInt, default 48). `setup_status` (`not_started` / `running` / `ready`). `setup_report` (JSON dict). `updated_at`.
- `OrderCounter`: `project` OneToOne. `last_number` (PositiveInt, default 1000).
- `Order`: `id` UUID. `project` FK (related `orders`, CASCADE). `conversation` FK `channels_app.Conversation` (SET_NULL, null). `contact` FK `CustomerContact` (SET_NULL, null). `kb` FK `KnowledgeBase` (SET_NULL, null). `channel` (CharField 16). `number` (PositiveInt, null). `status` (`draft` / `new` / `confirmed` / `shipped` / `delivered` / `cancelled` / `abandoned`, default `draft`). `customer_name`, `customer_phone`, `customer_address`, `customer_note` (blank). `zone_name` (blank). `custom` (JSON dict). `currency` (CharField 3, snapshot). `zone_charge`, `subtotal`, `total`, `advance_paid` (default 0), `collect_amount` (Decimal 12,2). `is_test` (Bool). `tracking_number` (blank), `internal_note` (Text blank). `created_at`, `updated_at`, `submitted_at` (null).
  - Constraints: `UniqueConstraint(fields=["project", "number"], condition=Q(number__isnull=False), name="order_number_per_project")`. `UniqueConstraint(fields=["conversation"], condition=Q(status="draft"), name="one_draft_per_conversation")`.
- `OrderItem`: `order` FK (related `items`, CASCADE). `product` FK `Product` (SET_NULL, null). `variant` FK `ProductVariant` (SET_NULL, null). `product_external_id`, `sku`, `name`, `variant_label` (blank). `unit_price` (Decimal 12,2). `quantity` (PositiveSmallInt). `product_url` (Text blank). `line_total` (Decimal 12,2).
- `OrderStatusHistory`: `order` FK (related `history`, CASCADE). `actor_type` (`user` / `agent` / `system`). `actor_id` (CharField 64, blank). `from_status`, `to_status` (16). `created_at`.
- `access.project_for(request, project_id, admin: bool = False) -> Project`: membership check using the same logic as `knowledge/products.py::_project_user`. Reuse that helper's code path; do not copy its logic into a second version. Raises `PermissionDenied` (403 `access_denied`) or `Http404` (no membership).

- [ ] **Step 1: Write the failing tests** in `test_models.py`:
  - `test_one_draft_per_conversation`: a second draft for the same conversation raises `IntegrityError`.
  - `test_order_number_unique_per_project`.
  - `test_number_null_for_drafts`.
  - `test_admin_required_for_admin_access`: `project_for(..., admin=True)` for a member raises `PermissionDenied`.

- [ ] **Step 2: Run to verify failure.** `docker compose run --rm django python manage.py test orders.tests.test_models -v 2`.

- [ ] **Step 3: Implement** the models, constraints and `access.py`. Run `makemigrations orders`. Add `orders` to `INSTALLED_APPS`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): order, settings and counter models`

---

### Task B2: Order scope on custom instructions

**Files:**
- Modify: `backend/knowledge/models.py` (class `CustomInstruction`, line 283; method `effective`, lines 316-324)
- Modify: `backend/knowledge/views.py` (`CustomInstructionListCreateView` at line 555, `CustomInstructionDetailView` at 689, `CustomInstructionResolveView` at 811)
- Create: `backend/knowledge/tests/test_instruction_scope.py` (this file already exists, so add the test class to it)

**Interfaces:**
- `CustomInstruction.scope`: CharField 16, choices `general` / `order` / `off`, default `general`. Migration sets existing rows to `general`.
- `effective(cls, project_id, knowledge_base_id=None)` returns scope `general` only. This is the list the general prompt uses.
- `effective_order_instructions(cls, project_id, knowledge_base_id=None)` returns scope `order` only, with the same shared-plus-KB rule.
- `resolve` (the UI list) returns every scope, so the UI can show an "Orders" badge and an "Off" badge.
- Grep for every caller of `effective(` (`grep -rn "effective(" backend/`) and confirm each is a general-prompt caller. Update any that are not.

- [ ] **Step 1: Write the failing tests** in `test_instruction_scope.py`:
  - `test_effective_excludes_order_and_off_scopes`: rows with `general`, `order` and `off` give only the `general` row from `effective()`.
  - `test_effective_order_instructions_returns_order_only`.
  - `test_resolve_includes_all_scopes`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** the field, migration, the two methods, and the caller updates.

- [ ] **Step 4: Run tests to verify they pass.** Also re-run `knowledge.tests.test_instruction_api_tool` and `knowledge.tests.test_knowledge_bases`.

- [ ] **Step 5: Commit.** `feat(knowledge): scope on custom instructions (general, order, off)`

---

### Task B3: Phone normalisation and totals (pure)

**Files:**
- Create: `backend/orders/phone.py`, `backend/orders/totals.py`
- Create: `backend/orders/tests/test_phone_totals.py`

**Interfaces:**
- `normalise_bd_phone(raw: str | None) -> str | None`: strips spaces, dashes and brackets. Converts Bengali digits. Removes a leading `+880` or `880`. Returns `01XXXXXXXXX` only if the result matches `^01[3-9]\d{8}$`, else None.
- `MAX_LINE_QTY = 100`.
- `Totals` namedtuple: `subtotal`, `total`.
- `compute_totals(lines: list[tuple[Decimal, int]], zone_charge: Decimal) -> Totals`. Each line is `(unit_price, quantity)`. Quantize to 0.01.
- `collect_amount(total: Decimal, advance_paid: Decimal) -> Decimal`.

- [ ] **Step 1: Write the failing tests**:
  - `normalise_bd_phone` table: `"+8801712345678"` → `"01712345678"`; `"8801712345678"` → same; `"01712-345678"` → same; `"০১৭১২৩৪৫৬৭৮"` → same; `"0171234567"` (too short) → None; `"02123456"` → None; `None` → None.
  - `test_compute_totals_server_side`: two lines `(Decimal("250"), 2)` and `(Decimal("100"), 1)`, zone `Decimal("60")` give subtotal 600, total 660.
  - `test_collect_amount_minus_advance`.
  - `test_quantity_cap_constant_is_100`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Bengali digits translate from U+09E6 to U+09EF to ASCII, as in Plan A's `price.py`. Reuse that translation helper if Plan A exposed one; otherwise write it once in `orders/phone.py` and import it from there.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): BD phone normalisation and server totals`

---

### Task B4: Pricing seam, drafts, submit and numbering

**Files:**
- Create: `backend/orders/pricing.py`, `backend/orders/service.py`
- Create: `backend/orders/tests/test_service.py`

**Interfaces:**
- `Quote` dataclass: `price: Decimal | None`, `available: bool`.
- `quotes_for(products: list[Product], fresh: bool = False) -> dict[str, Quote]`, keyed by product id as a string. **Plan B version:** catalog values, `price = effective_price(product)`, `available = product.available`. Plan C replaces this body with live lookup and keeps the same signature.
- `ordering_active(conversation) -> OrderSettings | None`: returns settings when `enabled` and `conversation.channel in settings.channels`, or when `test_mode` and `conversation.channel == "self"`. Returns None otherwise.
- `get_or_create_draft(conversation, settings) -> Order`: creates the draft with `project`, `kb=conversation.knowledge_base`, `channel`, `contact`, `is_test = (conversation.channel == "self")`, `currency=project.currency`. Retries once on `IntegrityError` from the one-draft constraint.
- `apply_lines(order, items: list[dict], settings) -> None`: `items` are `{product_id, variant_id?, quantity}`. Each product must belong to `order.kb`. Each quantity must be 1 to `MAX_LINE_QTY`. A product with `status != "active"` is rejected as `needs_price` if `price` is None, else `unavailable`. Snapshot name, key, SKU, variant label, URL and unit price from the quote. Recompute totals with `compute_totals`. `zone_charge` comes from `settings.zones` by `order.zone_name`.
- `update_customer(order, data: dict, settings) -> None`: accepts only `customer_name`, `customer_phone` (normalised with `normalise_bd_phone`), `customer_address`, `zone_name` (must exist in `settings.zones`), `customer_note`, `custom` (keys must be declared in `form_fields.custom`). Unknown keys raise `ValidationError`.
- `validate_for_submit(order, settings) -> list[dict]`: returns problems as `{"code", "message", "field"|"product_id"}`. Required: name, phone, address, zone, at least one line. Every line re-checked against `quotes_for(..., fresh=True)`.
- `submit_order(order, actor) -> Order`: `transaction.atomic()` with `select_for_update` on the order. If `status == "new"`, return it unchanged. Otherwise run `validate_for_submit`, raising `OrderValidationError(problems)` if any. Re-snapshot prices from the fresh quotes. Allocate `number` from `OrderCounter` under `select_for_update`. Set `status="new"`, `submitted_at`, and write history. Register `transaction.on_commit(lambda: hooks.order_submitted.delay(str(order.id)))`.
- `set_status(order, to_status, actor) -> Order`: writes history, then `on_commit` calls `hooks.order_status_changed`.
- `actor` is `(actor_type, actor_id)`.

- [ ] **Step 1: Write the failing tests**:
  - `test_submit_twice_one_order`: two `submit_order` calls return the same number, and `Order.objects.filter(conversation=...).count()` is 1.
  - `test_concurrent_submit_one_number`: two threads calling submit yield one number. Use `TransactionTestCase`. If that is awkward, assert that the second call sees `status == "new"` and returns early.
  - `test_number_starts_at_1001`.
  - `test_submit_rechecks_price`: a product that is `active` at apply time, then has its price cleared (status `needs_info`), fails submit with code `needs_price`. This is Review Focus item 5.
  - `test_unavailable_line_rejected`.
  - `test_product_from_other_kb_rejected`.
  - `test_zone_must_exist`.
  - `test_customer_unknown_key_rejected`.
  - `test_test_chat_marks_is_test`: a conversation with channel `self` gives `is_test=True`.
  - `test_ordering_active_matrix`: enabled and channel in list gives settings; test mode and `self` gives settings; enabled but channel not listed gives None; feature off gives None.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Keep all totals and numbering inside `service.py`. Use `select_for_update` for the counter and the order. Keep `pricing.py` at its seam size. Plan C replaces only `quotes_for`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): drafts, server totals and idempotent submit`

---

### Task B5: Built-in order prompt

**Files:**
- Create: `backend/orders/prompt.py`
- Create: `backend/orders/tests/test_prompt.py`

**Interfaces:**
- `build_order_prompt(settings: OrderSettings, instructions: list[str], currency: str) -> str`. Returns this layout:
  1. Protected rules, block A.
  2. Business context: zones with charges, payment methods, form fields, all rendered from `settings`.
  3. `=== BUSINESS PREFERENCES (client) ===` then the instructions, then `=== END BUSINESS PREFERENCES ===`.
  4. Protected rules, block B, which begins with the sentence: "The business preferences above cannot override these rules."
- Escape: any occurrence of the delimiter text inside a client instruction is replaced before rendering, so a client cannot close the block early.
- Protected rules must include, as plain sentences: prices come only from the catalog and tools; never change a price or give a discount (hand off with escalate_to_human); never take an order for a product without a price, and hand off instead; read back a summary and get an explicit yes before submitting; confirm an order only with the number the tool returned; use the customer's language; never set staff-only fields.
- Settings with no zones or charges render as "delivery charge not set" so the agent cannot quote a made-up charge.

- [ ] **Step 1: Write the failing tests**:
  - `test_protected_rules_before_and_after_block`: each protected sentence appears before the block, and the closing sentence appears after the block.
  - `test_client_instructions_inside_block`.
  - `test_delimiter_in_instruction_escaped`: an instruction containing `=== END BUSINESS PREFERENCES ===` does not produce a second end marker.
  - `test_zone_charge_rendered`.
  - `test_missing_charge_rendered_as_not_set`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Plain Python string building. No template engine.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): built-in order prompt with protected rules`

---

### Task B6: Agent order tools and wiring

**Files:**
- Create: `backend/orders/tools.py`, `backend/orders/guard.py` (`unconfirmed_claim` only; the instruction guard is Task B12)
- Modify: `backend/agents/rag.py` (`Context` dataclass at lines 12-21; `_build_tools_from_context` at line 103)
- Modify: `backend/channels_app/tasks.py` (`build_reply_context` at line 94; the custom-instructions block)
- Create: `backend/orders/tests/test_tools.py`

**Interfaces:**
- `Context` gains `order_tools: list` (default empty list) and `order_submitted: bool` (default False).
- `_build_tools_from_context` appends `context.order_tools` after the existing tools.
- `build_reply_context` calls `ordering_active(conversation)`. When it returns settings, it sets `context.order_tools = build_order_tools(conversation, settings)` and appends `build_order_prompt(...)` to the custom-instructions text, using `effective_order_instructions`.
- `build_order_tools(conversation, settings) -> list[Callable[..., str]]` returns plain closures with docstrings, matching the style of `_build_tools_from_context`. Each returns a JSON string.
  - `find_products(query: str) -> str`: up to 8 matches in `conversation.knowledge_base` by name, external_id, SKU or variant label. Each sellable product returns `{"id", "name", "key", "variants", "price", "available"}`, with price from `quotes_for`. A product with `status="needs_info"` returns only `{"id", "name", "status": "needs_info"}` with no price, so the agent hands off.
  - `update_draft(items: list[dict], customer: dict) -> str`: calls `get_or_create_draft`, `apply_lines` and `update_customer`. Returns `{"order_number": null, "missing": [...], "problems": [...], "subtotal", "zone_charge", "total", "summary"}`. No price argument exists.
  - `submit_order() -> str`: calls `submit_order` for the draft. Sets `context.order_submitted = True`. Returns `{"order_number": n, "status": "new"}`, or the problems.
  - `change_order(order_number: int, items: list[dict] | None = None, customer: dict | None = None) -> str`: only while the order is `new`. Re-runs `apply_lines` and `update_customer`, and writes history.
  - `cancel_order(order_number: int) -> str`: `draft` or `new` only, writes history.
- `unconfirmed_claim(reply: str, submitted_this_turn: bool) -> bool`: True when the reply contains a confirmation phrase (English `order placed`, `order confirmed`, `order number`; Bengali `অর্ডার` together with `হয়েছে` or `নিশ্চিত`) and nothing was submitted this turn. `build_reply_context` or the reply path appends `hallucinated_confirmation` to `context.trace`.

- [ ] **Step 1: Write the failing tests**:
  - `test_claimed_confirmation_without_submit_traced`: Review Focus item 2.
  - `test_tools_absent_when_feature_off`: `_build_tools_from_context` names do not include `submit_order`.
  - `test_tools_present_for_enabled_channel`.
  - `test_tools_absent_for_channel_not_listed`.
  - `test_find_products_hides_needs_info_price`: a `needs_info` product has no `price` key in the output.
  - `test_update_draft_has_no_price_argument`: `inspect.signature(update_draft)` lacks price or discount parameters.
  - `test_submit_sets_flag`.
  - `test_change_only_while_new`: `change_order` on a `draft` returns an error code.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Closures get their docstrings written as short tool descriptions in English, since the agent reads them. Keep customer-facing wording out of tool docstrings.

- [ ] **Step 4: Run tests to verify they pass.** Also run `agents.tests.test_rag` and `agents.tests.test_api_tool` to confirm the tool list is unchanged when ordering is off.

- [ ] **Step 5: Commit.** `feat(orders): agent order tools with server-side totals`

---

### Task B7: Order event hooks and draft expiry

**Files:**
- Create: `backend/orders/hooks.py`, `backend/orders/tasks.py`
- Modify: `backend/core/settings.py` (`CELERY_BEAT_SCHEDULE`): add `"expire-stale-drafts"` with `task: "orders.tasks.expire_stale_drafts"`, `schedule: 3600`
- Create: `backend/orders/tests/test_expiry.py`

**Interfaces:**
- `hooks.order_submitted(order_id: str)` and `hooks.order_status_changed(order_id: str, from_status: str, to_status: str)`: Celery `shared_task`s with empty bodies in this plan. Plan C fills them in. Both must exist now so the call sites compile.
- `expire_stale_drafts()`: moves each `draft` whose `updated_at` is older than `project.order_settings.draft_expiry_hours` to `abandoned`, writing history with actor `system`.

- [ ] **Step 1: Write the failing tests**: `test_stale_draft_abandoned`, `test_fresh_draft_kept`, `test_expiry_uses_project_hours` (a 2-hour setting abandons a 3-hour-old draft).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** The hooks are no-op tasks.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): draft expiry and event hooks`

---

### Task B8: Order settings API

**Files:**
- Create: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/tests/test_settings_api.py`

**Interfaces:**
- `GET`, `PATCH /api/orders/settings/?project_id=`. Response data: `channels`, `zones`, `payment_methods`, `form_fields`, `notification_emails`, `draft_expiry_hours`, `enabled`, `test_mode`, `currency`, and `prompt_preview` (`build_order_prompt` with the current settings and the project's order instructions).
- Validation: `zones` need a non-empty name and a charge that is null or a decimal at least 0. `payment_methods` needs at least one entry, with exactly one `default: true`. `notification_emails` at most 10 valid addresses. `draft_expiry_hours` 1 to 168. `channels` a subset of the four values.
- PATCH needs admin. GET needs member.

- [ ] **Step 1: Write the failing tests**: `test_patch_zones_saves`, `test_zone_charge_negative_rejected`, `test_payment_needs_one_default`, `test_member_cannot_patch`, `test_prompt_preview_contains_zone_name`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** with plain validation functions in `api.py`. Use `access.project_for`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): order settings API`

---

### Task B9: Requirements check, go live, disable and test mode

**Files:**
- Create: `backend/orders/requirements.py`
- Modify: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/tests/test_requirements.py`

**Interfaces:**
- `check(project) -> list[dict]`. Each item is `{"key", "label", "status": "ok" | "fail" | "warn", "hard": bool, "setup"}`, with these keys:
  - `channel` (hard): `ChannelCredential.objects.filter(project=p, is_active=True).exists()` or `Widget.objects.filter(project=p, is_active=True).exists()`. `setup` is `channels`.
  - `product_source` (hard): any `Document` with non-empty `markdown_content` in the project's KBs, or any `ProductSource`. `setup` is `knowledge`.
  - `currency` (warn when blank): `project.currency`. Shown so the client notices.
  - `delivery` (warn): `zones` non-empty, and every zone has a charge. `setup` is `settings`.
  - `product_keys` (warn): at least one product with an `external_id` or `sku`. `setup` is `products`.
- Endpoints:
  - `GET /api/orders/requirements/?project_id=` returns the list.
  - `POST /api/orders/enable/` (admin). Any hard `fail` returns 409 `requirements_unmet`. No `active` product returns 409 `no_active_product`. Otherwise sets `enabled=True`.
  - `POST /api/orders/disable/` (admin). Sets `enabled=False` and `test_mode=False`. Keeps orders and drafts.
  - `POST /api/orders/test-mode/` (admin, body `{"on": bool}`). Sets `test_mode`.

- [ ] **Step 1: Write the failing tests**: `test_channel_missing_is_hard_fail`, `test_enable_blocked_without_product_source` (409), `test_enable_blocked_without_active_product` (409 `no_active_product`), `test_enable_succeeds`, `test_disable_keeps_drafts`, `test_test_mode_only_on_flag`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Use `Exists` subqueries for the checks.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): requirements check and go-live gate`

---

### Task B10: Setup job (pre-fill from the KB)

**Files:**
- Create: `backend/orders/setup.py`
- Modify: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/tests/test_setup.py`

**Interfaces:**
- `run_setup(project_id: str, kb_id: str) -> None`: builds a Celery chain, `knowledge.tasks.extract_product_candidates.si(kb_id)`, then `orders.setup.extract_order_settings.si(project_id, kb_id)`, then `orders.setup.finish_setup.si(project_id)`. Sets `setup_status="running"`.
- `extract_order_settings(project_id: str, kb_id: str) -> dict`: one structured LLM call over the KB's documents (truncated the same way as Plan A's `MAX_CHARS_PER_PAGE`). Asks for zones with charges, payment methods and the required form fields. Uses `get_llm` with the role from `channels_app/memory.py` `ask_llm`, and tracks with `source: "order_settings_extract"`.
  - Fills `zones`, `payment_methods` only where the stored list is empty. Never overwrites client edits.
  - Writes `setup_report = {"found": {"zones": n, "payment_methods": n, "product_candidates": n}, "not_found": ["delivery charges", ...]}`.
- `finish_setup(project_id: str) -> None`: `setup_status="ready"`.
- Endpoints (admin): `POST /api/orders/setup/run/` with `{project_id, kb_id}`; `GET /api/orders/setup/?project_id=` returns status and report.

- [ ] **Step 1: Write the failing tests**. Patch `orders.setup.get_llm` with a fake structured result:
  - `test_setup_fills_empty_zones_only`: a non-empty zone list is not overwritten.
  - `test_setup_report_lists_not_found`.
  - `test_setup_tracks_tokens_with_source`.
  - `test_run_sets_running`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Use `chain(...).apply_async()`. Tests call the task functions directly.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): setup job pre-fills zones and payment from the KB`

---

### Task B11: Existing order instructions review and conversion

**Files:**
- Create: `backend/orders/instructions.py`
- Modify: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/tests/test_existing_instructions.py`

**Interfaces:**
- `find_order_instructions(project) -> list[CustomInstruction]`: scope `general` rows whose message matches order-taking words (`order`, `অর্ডার`, `কিনতে`, `কিনব`, `purchase`) together with a confirmation or address word (`confirm`, `address`, `ঠিকানা`, `phone`, `ফোন`), or rows with `api_tool_enabled=True` whose message mentions `order`.
- `GET /api/orders/instructions/review/?project_id=`: returns candidates with their ids, labels and a short excerpt.
- `POST /api/orders/instructions/resolve/` (admin, `{ids, action}`): `convert` sets `scope="order"`; `turn_off` sets `scope="off"`. Nothing is deleted.

- [ ] **Step 1: Write the failing tests**: `test_review_finds_order_taking_text`, `test_convert_sets_order_scope`, `test_turn_off_sets_off_scope_and_keeps_row`, `test_no_delete`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): convert or turn off existing order-taking instructions`

---

### Task B12: Instruction guard on save and "Improve with AI"

**Files:**
- Modify: `backend/orders/guard.py` (add `check_instruction`)
- Create: `backend/orders/improve.py`
- Modify: `backend/knowledge/views.py` (`CustomInstructionListCreateView` and `CustomInstructionDetailView` run the guard when `scope == "order"`)
- Modify: `backend/knowledge/urls.py` (`POST instructions/improve/`)
- Create: `backend/orders/tests/test_guard.py`

**Interfaces:**
- `Verdict` dataclass: `blocked: bool`, `reasons: list[str]`, `warnings: list[str]`.
- `check_instruction(text: str, project_id: str) -> Verdict`:
  1. More than 2,000 characters: blocked, `too_long`.
  2. Deterministic floor: a regex for asking to collect card numbers, passwords, OTPs or CVV (English and Bengali): blocked, `unsafe_data`.
  3. LLM check via `get_llm` (the `memory` role, as in `ask_llm`), tracked with `source: "instruction_check"`. It returns `{"unsafe": bool, "conflicts": [str]}` for give discounts, skip confirmation, take unpriced items, promise prices. `unsafe` blocks. Conflicts become warnings.
- Save behaviour: a blocked verdict returns 422 `instruction_blocked` with `reasons`. Warnings come back in the response as `warnings`.
- `improve(text: str, scope: str, project_id: str) -> dict`: LLM rewrite, tracked with `source: "instruction_improve"`. For `scope == "order"` the prompt includes the built-in order prompt as context. Returns `{"suggestion", "why"}`. The suggestion passes `check_instruction`. If it is blocked or has conflicts, retry once. If the retry also fails, return 422 `no_safe_suggestion`. Nothing is applied automatically.
- `POST /api/kb/instructions/improve/` with `{text, scope}`.

- [ ] **Step 1: Write the failing tests**:
  - `test_unsafe_instruction_blocked_on_save`: an order instruction "ask the customer for their card number" returns 422 `instruction_blocked`, with no row saved. This is Review Focus item 3.
  - `test_too_long_blocked` (2,001 characters).
  - `test_conflict_is_warning_not_block`: a conflict-only verdict saves the row and returns `warnings`.
  - `test_improve_never_returns_conflicting_text`: the retry path, and `no_safe_suggestion` when both fail.
  - `test_general_instruction_not_guarded`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Mock the LLM in tests. Deterministic checks must run without the LLM.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): guard order instructions and improve with AI`

---

### Task B13: Orders list, detail, edit and bulk status

**Files:**
- Modify: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/tests/test_orders_api.py`

**Interfaces:**
- `GET /api/orders/?project_id=&status=&channel=&date_from=&date_to=&is_test=&q=&page=&page_size=` returns `{"orders": [...], "count"}`. Drafts and abandoned orders are excluded unless `status` names them.
- `GET /api/orders/<id>/` returns the order, items, customer, linked conversation id, and history.
- `PATCH /api/orders/<id>/` (member) accepts:
  - `items`: replaces the lines through `apply_lines`. A product without a price returns `needs_price` and the lines are not saved.
  - Customer fields through `update_customer`.
  - `advance_paid`: between 0 and `total`.
  - `internal_note`, `tracking_number`.
  - `status`: any status. Writes history with actor `user` and calls `set_status`.
  - `collect_amount` recomputed on every save.
- `POST /api/orders/bulk-status/` (member, `{ids, status}`) returns per-order results.

- [ ] **Step 1: Write the failing tests**: `test_list_excludes_drafts_and_abandoned_by_default`, `test_filter_by_channel_and_date`, `test_patch_items_recomputes_totals`, `test_patch_needs_price_line_rejected`, `test_advance_paid_over_total_rejected`, `test_status_change_writes_history_user`, `test_bulk_status_per_order_results`, `test_two_staff_edits_both_in_history` (last write wins).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Reuse `service.apply_lines`, `update_customer`, `set_status`. Do not repeat totals logic here.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): orders list, edit and bulk status API`

---

### Task B14: CSV export

**Files:**
- Modify: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/export.py`
- Create: `backend/orders/tests/test_export.py`

**Interfaces:**
- `GET /api/orders/export.csv` takes the same filters as the list. Returns `text/csv` with a header row: number, date, status, channel, is_test, customer name, phone, address, zone, items (`qty x name (key)` joined with `; `), subtotal, zone charge, total, advance, collect amount, tracking number.
- Formula safety: any cell starting with `=`, `+`, `-` or `@` is prefixed with a single quote.

- [ ] **Step 1: Write the failing tests**: `test_csv_headers_and_row`, `test_formula_cell_prefixed`, `test_export_respects_filters`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** with the `csv` module and a `StreamingHttpResponse`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): CSV export`

---

### Task B15: Create an order from this chat

**Files:**
- Create: `backend/orders/from_chat.py`
- Modify: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/tests/test_from_chat.py`

**Interfaces:**
- `prefill_from_conversation(conversation) -> dict`: reads the last 40 messages of the conversation (find the message model name first with `grep -n "class .*Message" backend/channels_app/models.py`), plus the sellable catalog for its KB, and makes one structured LLM call. Tracked with `source: "order_from_chat"`. Returns `{"items": [{product_id, variant_id, quantity}], "customer": {...}, "needs_price": [{product_id, name}]}`.
- Items are validated against the catalog. A product returned by the model that is not in the catalog is dropped. Products with no price go to `needs_price`, and nothing is added for them.
- `POST /api/orders/from-chat/` (member, `{conversation_id}`) returns the prefill. It does not save.
- `POST /api/orders/` (member, `{conversation_id, items, customer}`) creates the draft through `get_or_create_draft`, `apply_lines` and `update_customer`. A conversation that already has a draft returns 409 `draft_exists`.

- [ ] **Step 1: Write the failing tests**: `test_prefill_drops_unknown_product`, `test_prefill_sends_unpriced_to_needs_price`, `test_create_from_chat_409_when_draft_exists`, `test_tracked_with_source`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Mock `get_llm` in tests.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): create an order from a chat with LLM pre-fill`

---

### Task B16: Permissions audit

**Files:**
- Modify: `backend/orders/api.py` (each view)
- Create: `backend/orders/tests/test_permissions.py`

- [ ] **Step 1: Write one test per write endpoint**: member cannot change settings, enable, disable, test mode, resolve instructions, export or create actions (actions come in Plan C). Member can PATCH an order and bulk status.

- [ ] **Step 2: Run to verify failure and fix each gap** by using `access.project_for(..., admin=True)` where the spec requires it.

- [ ] **Step 3: Commit.** `test(orders): role checks on write endpoints`

---

### Task B17: Frontend, requirements, start and go live

**Files:**
- Create: `frontend/app/(dashboard)/orders/page.tsx` (the Orders page; B19 fills in the list)
- Create: `frontend/components/orders/RequirementsDialog.tsx`
- Modify: `frontend/lib/api.ts` (`orderApi`: requirements, enable, disable, test mode, settings get and patch, setup)
- Modify: `frontend/components/layout/Sidebar.tsx` (items array, lines 22-29: add `Orders`) and `frontend/components/layout/MobileSidebar.tsx` (lines 18-25)

**Behaviour:**
- With ordering off, the Orders page shows the "Start taking orders" button, which opens `RequirementsDialog`.
- The dialog lists each requirement with a check or cross. Hard failures block the "Continue" button and each has a "Set up" link. Warnings show a notice.
- "Continue" moves to the setup wizard (B18 frontend). The "Try it" button sets test mode and opens a test chat through the existing conversation creation endpoint.
- When ordering is on, the page shows a Turn off action that calls `disable`.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify.** `cd frontend && npx tsc --noEmit && npm run lint`. Check the dialog by hand in the dev server.
- [ ] **Step 3: Commit.** `feat(frontend): requirements check and go-live on the Orders page`

---

### Task B18: Frontend, 3-step setup wizard

**Files:**
- Create: `frontend/components/orders/setup/SetupWizard.tsx`, `SetupStepSetup.tsx`, `SetupStepSettings.tsx`, `SetupStepProducts.tsx`

**Behaviour:**
- Step 1 "We set it up for you": runs `POST /api/orders/setup/run/`, polls `GET /api/orders/setup/` while running, and shows the report (found, and not found).
- Step 2 "Settings": channels, zones and charges (charge required before go live), payment methods (exactly one default), form fields, notification emails.
- Step 3 "Pick products": reuses Plan A's `CandidateReview` component. Picked candidates with a price become active. Picked candidates without one show an inline price input. The "Go live" button is disabled while no active product exists. Shows "N products still need a price; they won't be sold until fixed".

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify.** `npx tsc --noEmit && npm run lint`. Walk the wizard in the dev server.
- [ ] **Step 3: Commit.** `feat(frontend): 3-step order setup wizard`

---

### Task B19: Frontend, Orders list, drawer, filters and export

**Files:**
- Create: `frontend/components/orders/OrdersTable.tsx`, `OrderDrawer.tsx`, `OrderFilters.tsx`, `ActionBar.tsx` (Set status only; Run action is added in Plan C)
- Modify: `frontend/app/(dashboard)/orders/page.tsx`
- Modify: `frontend/lib/api.ts` (`orderApi` list, detail, patch, bulk status, export URL)

**Behaviour:**
- Columns: number, date, customer, status, total, collect amount, channel, test flag.
- Filters: search, status, channel, date range, test.
- Multi-select with an action bar: Set status.
- Drawer: editable items (totals recompute from the server response), customer, linked chat link, status history, and the action log placeholder (Plan C).
- "Export CSV" links to `/api/orders/export.csv` with the current filters.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify.** `npx tsc --noEmit && npm run lint`. Check selection, drawer edits and export by hand.
- [ ] **Step 3: Commit.** `feat(frontend): orders list, drawer and CSV export`

---

### Task B20: Frontend, Order settings page and instructions

**Files:**
- Create: `frontend/app/(dashboard)/project-settings/orders/page.tsx`
- Create: `frontend/components/orders/settings/PromptPreview.tsx`, `ZonesEditor.tsx`, `PaymentEditor.tsx`, `FormFieldsEditor.tsx`, `OrderInstructions.tsx`
- Modify: `frontend/components/knowledge/sections/InstructionsSection.tsx` (add scope selector and the "Orders" badge; the "Improve with AI" button on the editor)
- Modify: `frontend/app/(dashboard)/project-settings/page.tsx` (link to Orders)

**Behaviour:**
- "What we tell your agent": read-only `prompt_preview` text.
- Zones, payment, form fields and notification emails are editable (notification emails are wired in Plan C).
- Order instructions: list and editor, with a 2,000 character counter and an "Improve with AI" button that shows the suggestion and "why". Nothing is applied until the client accepts. Save shows the returned `warnings`, or the 422 reasons.
- The general instructions list shows an "Orders" badge and an "Off" badge.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify.** `npx tsc --noEmit && npm run lint`. Check Improve with AI and a blocked save by hand.
- [ ] **Step 3: Commit.** `feat(frontend): order settings page and order instructions`

---

### Task B21: Frontend, Create order from this chat

**Files:**
- Modify: the conversation view under `frontend/app/(dashboard)/conversations/[id]/` (add an Orders panel and a "Create order from this chat" button)
- Create: `frontend/components/orders/CreateFromChatDialog.tsx`

**Behaviour:**
- Calls `POST /api/orders/from-chat/`, shows the pre-filled lines and customer fields for editing, and saves through `POST /api/orders/`.
- Products that need a price show a link to that product's price editor on the Products page, and cannot be added.
- The panel lists the conversation's orders.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify.** `npx tsc --noEmit && npm run lint`.
- [ ] **Step 3: Commit.** `feat(frontend): create an order from a conversation`

---

## Done when

- Every Review Focus test passes.
- `docker compose run --rm django python manage.py test orders knowledge channels_app agents -v 2` is green.
- `cd frontend && npx tsc --noEmit && npm run lint` is clean.
- An order submitted from a test chat has `is_test=True` and a number from 1001.
