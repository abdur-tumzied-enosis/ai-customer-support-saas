# AI Cost Monitoring (first slice) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Snapshot the model rate on every new `UsageEvent`, and give superusers a Django admin cost report by provider and model, with unpriced usage made visible.

**Architecture:** `usage/utils.py` gets one pricing function that returns a per-event snapshot (cost, source, provider, model, rates). Every LLM call site passes its snapshot into `track_token_usage`. A read-only admin page in the `usage` app aggregates events for a date range and project. Only superusers can open it.

**Tech Stack:** Django 5 ORM, Django admin, Decimal money, Django TestCase, Docker (`docker compose exec django ...`).

**Spec:** `docs/superpowers/specs/2026-11-10-ai-cost-monitoring-design.md`

## Global Constraints

- Money and rates are `Decimal`, never float. Rates are `max_digits=12, decimal_places=6` (matches `AIModel`).
- Unknown cost is `cost_usd = NULL` with `cost_source = "unknown"`, never `0`.
- Embeddings and image events are `cost_source = "system_default"` with `cost_usd = Decimal(0)`.
- Price changes never alter stored event costs. Snapshot fields are written once and never updated.
- The report page is superuser-only (`is_superuser`), not `is_staff`.
- No customer-facing API or UI changes. Existing `billing/tests/test_api.py` FORBIDDEN-field test must still pass.
- Test command: `docker compose exec django python manage.py test <module> --noinput`.
- Match the indentation of each file you edit (agents, usage and orders files use tabs).

## Review Focus

1. Google model with no matching `AIModel` row → event is `unknown`, `cost_usd` NULL, not zero. (Task 2)
2. A call whose messages mix a priced and an unpriced model → whole call is `unknown`, as today. (Task 2)
3. Price changed after an event was written → that event's cost and rates are unchanged. (Task 2)
4. Legacy rows (blank `cost_source`, cost present) appear as "legacy", never re-priced or shown as zero. (Task 4)
5. Staff who are not superusers get a denied response on the report and on the usage event changelist. (Task 5)

---

### Task 1: Snapshot fields on UsageEvent

**Files:**
- Modify: `backend/usage/models.py:148-158` (add fields after `cost_usd`)
- Create: `backend/usage/migrations/0006_usageevent_pricing_snapshot.py` (generated)
- Test: `backend/usage/tests/test_pricing_snapshot.py`

**Interfaces:**
- Consumes: none
- Produces: `UsageEvent` fields `provider` (CharField 16, blank, default ""), `model_name` (CharField 200, blank, default ""), `price_in_per_mtok` and `price_out_per_mtok` (DecimalField 12,6, null, blank), `cost_source` (CharField 16, blank, default ""; choices `provider_reported`, `token_calculated`, `unknown`, `system_default`).

- [ ] **Step 1: Write the failing test**

```python
from decimal import Decimal
from django.test import TestCase
from accounts.models import Project
from usage.models import UsageEvent


class PricingSnapshotFieldsTest(TestCase):
    def test_snapshot_fields_round_trip(self):
        project = Project.objects.create(name="p")
        e = UsageEvent.objects.create(
            project=project, kind="chat_in", tokens=10, cost_usd=Decimal("0.001234"),
            provider="google", model_name="gemini-3.8-flash",
            price_in_per_mtok=Decimal("0.300000"), price_out_per_mtok=Decimal("2.500000"),
            cost_source="token_calculated",
        )
        e.refresh_from_db()
        assert e.price_in_per_mtok == Decimal("0.300000")
        assert e.cost_source == "token_calculated"

    def test_legacy_event_defaults(self):
        project = Project.objects.create(name="p")
        e = UsageEvent.objects.create(project=project, kind="chat_in", tokens=1)
        assert e.cost_source == "" and e.provider == "" and e.price_in_per_mtok is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec django python manage.py test usage.tests.test_pricing_snapshot --noinput`
Expected: FAIL with `TypeError: UsageEvent() got unexpected keyword arguments: 'provider'`

- [ ] **Step 3: Add the fields in `backend/usage/models.py`**

Add after the `cost_usd` line (line 150). Use `COST_SOURCE_CHOICES = [("provider_reported", ...), ("token_calculated", ...), ("unknown", ...), ("system_default", ...)]` as a class attribute. No other model changes.

- [ ] **Step 4: Generate the migration**

Run: `docker compose exec django python manage.py makemigrations usage -n usageevent_pricing_snapshot`
Expected: creates `0006_usageevent_pricing_snapshot.py` depending on `0005_self_hosted_cost_zero`. Only `AddField` operations.

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose exec django python manage.py test usage.tests.test_pricing_snapshot --noinput`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/usage/models.py backend/usage/migrations/0006_usageevent_pricing_snapshot.py backend/usage/tests/test_pricing_snapshot.py
git commit -m "feat(usage): add pricing snapshot fields to UsageEvent"
```

---

### Task 2: Pricing snapshot in `track_token_usage`

**Files:**
- Modify: `backend/usage/utils.py:26-92` (`track_token_usage`, `llm_costs`)
- Test: `backend/usage/tests/test_pricing_snapshot.py` (extend)

**Interfaces:**
- Consumes: Task 1 fields.
- Produces:
  - `llm_pricing(messages) -> dict` returning `{"chat_in": P, "chat_out": P}` where `P` is a dict with keys `cost_usd` (Decimal|None), `cost_source` (str), `provider` (str), `model_name` (str), `price_in_per_mtok` (Decimal|None), `price_out_per_mtok` (Decimal|None), and optional `cost_note` (str).
  - `llm_costs(messages) -> (Decimal|None, Decimal|None)` kept as a wrapper over `llm_pricing` so existing callers and `agents/tests/test_model_config.py` still pass.
  - `track_token_usage(project_id, kind, tokens, metadata=None, pricing=None, conversation_id=None, message_id=None) -> None`. `pricing` is a dict with the `P` keys (all optional). It replaces the old `cost_usd` argument. When `cost_note` is present it is stored in `metadata["cost_note"]`.

**Rulings to apply (already decided, record in the ledger):**
- Provider is inferred from the message: `"openrouter"` when `response_metadata` has a `cost` key or the model name contains `/`, otherwise `"google"`. Risk: a future Google id containing `/` would be misclassified.
- `AIModel` match uses `provider` and `name` (exact).
- OpenRouter: `cost_usd` is the charged total on `chat_out`. `chat_in` gets `cost_usd = Decimal(0)`, `cost_source = "provider_reported"`, and `cost_note = "cost booked on chat_out"`.
- Google with a matching priced `AIModel`: `token_calculated`. Without one: `unknown`, cost NULL.
- Messages with several model names: `model_name` is the names joined by `,` and both rates are NULL. Cost is still summed per message.

- [ ] **Step 1: Write the failing tests** (in `test_pricing_snapshot.py`)

Cover these cases, each as its own test method:
- `test_google_priced_snapshot`: AIMessage with `response_metadata={"model_name": "gemini-3.8-flash"}`, `usage_metadata={"input_tokens": 10000, "output_tokens": 2000}`, an `AIModel(provider="google", name="gemini-3.8-flash", input_price_per_mtok=0.30, output_price_per_mtok=2.50)`. Assert `llm_pricing([m])["chat_out"]["cost_usd"] == Decimal("0.005")`, `cost_source == "token_calculated"`, `provider == "google"`, `price_out_per_mtok == Decimal("2.500000")`.
- `test_google_unpriced_is_unknown_not_zero`: no AIModel row → `cost_usd is None`, `cost_source == "unknown"`.
- `test_openrouter_booked_on_output`: message with `response_metadata={"model_name": "deepseek/x", "cost": 0.00042}`. `chat_out.cost_usd == Decimal("0.00042")`, `chat_in.cost_usd == Decimal(0)`, `chat_in.cost_source == "provider_reported"`, `chat_in.cost_note` set, `provider == "openrouter"`.
- `test_mixed_messages_unknown`: one priced Google message plus one unpriced → both `cost_usd` None.
- `test_match_requires_provider`: an `AIModel(provider="openrouter", name="gemini-3.8-flash")` does not price a Google message.
- `test_price_change_keeps_old_event`: track an event with a snapshot, then update the AIModel rate, then re-read the event → rate and cost unchanged.
- `test_track_token_usage_writes_snapshot`: call `track_token_usage(...)` with `pricing=...`, read back the event: all snapshot fields set, `metadata["cost_note"]` present when given.
- `test_llm_costs_still_returns_pair`: `llm_costs([m]) == (cost_in, cost_out)` for the priced Google case.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec django python manage.py test usage.tests.test_pricing_snapshot --noinput`
Expected: FAIL with `ImportError: cannot import name 'llm_pricing'`

- [ ] **Step 3: Implement `llm_pricing`, wrap `llm_costs`, and change `track_token_usage` in `backend/usage/utils.py`**

Keep the "never raises" contract: `llm_pricing` returns all-unknown `P` dicts on exception. `track_token_usage` keeps its try/except. Use `AIModel.objects.filter(provider=..., name=..., input_price_per_mtok__isnull=False, output_price_per_mtok__isnull=False).first()`. Replace the `cost_usd=` argument with `pricing=` and map its keys onto the `UsageEvent` fields.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec django python manage.py test usage agents.tests.test_model_config --noinput`
Expected: PASS. The existing `test_attribution.py` and `test_model_config.py` tests must pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/usage/utils.py backend/usage/tests/test_pricing_snapshot.py
git commit -m "feat(usage): snapshot model rates and cost source on each event"
```

---

### Task 3: Pass snapshots from every call site

**Files:**
- Modify (LLM call sites, each currently `cost_in, cost_out = llm_costs([...])` then two `track_token_usage(... cost_usd=...)` calls):
  - `backend/knowledge/product_extract.py:68-71`
  - `backend/knowledge/product_suggest.py:76-78`
  - `backend/channels_app/memory.py:56-58`
  - `backend/orders/improve.py:40-42`
  - `backend/orders/guard.py:62-64`
  - `backend/orders/from_chat.py:54-56`
  - `backend/orders/setup.py:74-76`
  - `backend/agents/rag.py:199-224` (`_track_token_usage`)
- Modify (non-LLM): `backend/knowledge/tasks.py:299-309` (embed) and `backend/usage/utils.py` `record_image_usage` (image).
- Test: `backend/agents/tests/test_track_snapshot.py` (new) plus extend `backend/usage/tests/test_pricing_snapshot.py`.

**Interfaces:**
- Consumes: `llm_pricing(messages)` and the `pricing=` argument from Task 2.
- Produces: no new interfaces.

- [ ] **Step 1: Write the failing tests**

- `test_rag_snapshot`: run `agents.rag._track_token_usage` with an `AIMessage` (Google, priced) and assert the `chat_out` event has `provider == "google"`, `cost_source == "token_calculated"`.
- `test_embed_is_system_default`: call the embedding tracking path (or `track_token_usage` with the same arguments the embed task now passes). Assert `cost_usd == Decimal(0)` and `cost_source == "system_default"`. Use the smallest existing test seam; if there isn't one, assert on `track_token_usage` directly with the embed arguments.
- `test_image_is_system_default`: `record_image_usage(project_id, "index")` → `cost_source == "system_default"`, `cost_usd == Decimal(0)`.
- One test per call-site module is NOT needed. Existing tests in those modules must keep passing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_track_snapshot usage.tests.test_pricing_snapshot --noinput`
Expected: FAIL on `cost_source` assertions (field is blank).

- [ ] **Step 3: Update each call site**

For each LLM site: replace `cost_in, cost_out = llm_costs([X])` with `pricing = llm_pricing([X])`, and pass `pricing=pricing["chat_in"]` to the input event and `pricing=pricing["chat_out"]` to the output event. Remove `cost_usd=`. Keep the message lists and attribution as they are.

For `knowledge/tasks.py` embed: replace `cost_usd=Decimal(0)` with `pricing={"cost_usd": Decimal(0), "cost_source": "system_default"}`. For `record_image_usage`: add `cost_source="system_default"` to the create call. Remove now-unused imports only if they become unused.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec django python manage.py test agents knowledge orders channels_app usage --noinput`
Expected: PASS. Any failure in those suites must be fixed before moving on.

- [ ] **Step 5: Commit**

```bash
git add backend/knowledge backend/channels_app/memory.py backend/orders backend/agents/rag.py backend/usage/utils.py backend/agents/tests/test_track_snapshot.py backend/usage/tests/test_pricing_snapshot.py
git commit -m "feat(usage): record pricing snapshot at every LLM and embedding call site"
```

---

### Task 4: Cost report aggregation

**Files:**
- Create: `backend/usage/report.py`
- Test: `backend/usage/tests/test_cost_report.py`

**Interfaces:**
- Consumes: Task 1–3 fields.
- Produces: `ai_cost_report(start: date, end: date, project_id: str | None = None) -> dict` with keys:
  - `known_by_source`: `{cost_source: Decimal}` for `provider_reported`, `token_calculated`, `system_default`.
  - `unknown_events`: int (rows with `cost_usd` NULL), `unknown_tokens`: int.
  - `legacy_events`: int (rows with blank `cost_source` and `cost_usd` present).
  - `credits_charged`: Decimal, debits from `billing.CreditTransaction` (`kind="charge"`) over the same dates and project (`wallet__project_id`).
  - `models`: list of dicts `{provider, model_name, events, input_tokens, output_tokens, known_usd, unknown_events, unknown_tokens}` sorted by `known_usd` descending. `input_tokens` = tokens on `chat_in`, `output_tokens` = tokens on `chat_out`.
  - `unmatched_models`: sorted list of distinct `model_name` values that have unknown events.
  - `has_unknown`: bool.

Dates use `created_at__date__gte/lte` with inclusive bounds, same as `billing/report.py` `coverage`. Use `Decimal("0")` as the default for empty sums.

- [ ] **Step 1: Write the failing tests** (`test_cost_report.py`, `TestCase`, one project plus one event per case)

- `test_known_by_source_split`: one `token_calculated` event at 0.005, one `provider_reported` at 0.001, one `system_default` at 0 → `known_by_source` values match, `unknown_events == 0`.
- `test_unknown_counted_not_zeroed`: a `cost_source="unknown"` event with `cost_usd=None` and 500 tokens → `unknown_events == 1`, `unknown_tokens == 500`, `has_unknown is True`, its model name in `unmatched_models`.
- `test_legacy_rows_separate`: blank `cost_source`, `cost_usd=Decimal("0.2")` → `legacy_events == 1`, not in `known_by_source`.
- `test_models_table_tokens`: a `chat_in` 1000 and `chat_out` 300 for the same model → `input_tokens == 1000`, `output_tokens == 300`.
- `test_date_and_project_filter`: an event outside the range and one in another project are excluded.
- `test_credits_charged_debits_only`: a charge row and a grant row → `credits_charged` equals the charge's absolute amount only. Use `billing.service` or create `CreditWallet`/`CreditTransaction` rows directly.
- `test_unknown_project_id_empty`: an unknown or invalid project id returns zeroed totals and does not raise.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec django python manage.py test usage.tests.test_cost_report --noinput`
Expected: FAIL with `ModuleNotFoundError: No module named 'usage.report'`

- [ ] **Step 3: Implement `ai_cost_report` in `backend/usage/report.py`**

Use `UsageEvent.objects.filter(...)` and `.values(...).annotate(...)`. Do the per-model roll-up in Python from one grouped query on `provider, model_name, kind, cost_source` (fine for MVP volume). Convert the project id with `Project.objects.filter(pk=...)` inside a `try/except (ValueError, ValidationError)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec django python manage.py test usage.tests.test_cost_report --noinput`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/usage/report.py backend/usage/tests/test_cost_report.py
git commit -m "feat(usage): aggregate AI cost by source, model and project"
```

---

### Task 5: Superuser-only admin report page

**Files:**
- Modify: `backend/usage/admin.py` (create the file if it does not exist; register `UsageEvent`)
- Create: `backend/templates/admin/usage/ai_cost_report.html` (match the template folder used by `backend/billing/templates/admin/billing/coverage.html`; if that folder lives under `backend/billing/templates/`, put this under `backend/usage/templates/admin/usage/` instead)
- Test: `backend/usage/tests/test_admin_report.py`

**Interfaces:**
- Consumes: `report.ai_cost_report` from Task 4.
- Produces: admin URL `admin:usage_usageevent_report` (path `report/` under the UsageEvent admin). Query params `start`, `end` (ISO dates, default last 30 days, same parsing as `billing/admin.py` `coverage_view`), `project` (optional project id).

**Ruling:** Access is superuser-only. `has_view_permission` and `has_module_permission` return `request.user.is_superuser`; add, change and delete return False. The report view also checks `is_superuser` and returns `HttpResponseForbidden()` for others (same pattern as `agents/admin.py`, not `PermissionDenied`).

- [ ] **Step 1: Write the failing tests**

- `test_staff_non_superuser_denied`: staff user (`is_staff=True`, `is_superuser=False`) GET the report URL → status 403.
- `test_superuser_sees_report`: superuser GET → 200, context contains `models` and `known_by_source`.
- `test_unknown_banner_shown`: an unknown event in range → response contains the unknown banner text (use a fixed string from the template, e.g. "unpriced").
- `test_changelist_superuser_only`: staff non-superuser GET `admin:usage_usageevent_changelist` → 403 (or redirect to admin login, whichever Django returns for no view permission; assert it is not 200).

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec django python manage.py test usage.tests.test_admin_report --noinput`
Expected: FAIL with `NoReverseMatch` for `usage_usageevent_report`

- [ ] **Step 3: Implement the admin and template**

`UsageEventAdmin` with `list_display = ("created_at", "project", "kind", "model_name", "tokens", "cost_usd", "cost_source")`, `list_filter = ("kind", "cost_source", "provider")`, read-only (no add/change/delete). Override `get_urls` to add `path("report/", self.admin_site.admin_view(self.report_view), name="usage_usageevent_report")`. Template: one table for overview totals (known by source, unknown count and tokens, credits charged), the per-model table, the unmatched-model list, and a warning block shown only when `has_unknown`. Use `{% if has_unknown %}` with the word "unpriced" in the warning. Keep the template plain; no charts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec django python manage.py test usage.tests.test_admin_report --noinput`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/usage/admin.py backend/usage/tests/test_admin_report.py backend/usage/templates backend/templates
git commit -m "feat(usage): superuser-only AI cost report in Django admin"
```

---

### Task 6: Full regression run

**Files:** none changed unless a regression is found.

- [ ] **Step 1: Run the billing customer-API regression test**

Run: `docker compose exec django python manage.py test billing --noinput`
Expected: PASS. The FORBIDDEN-field test (`billing/tests/test_api.py`) must still pass; no new field may appear in customer responses.

- [ ] **Step 2: Run the whole backend suite**

Run: `docker compose exec django python manage.py test --noinput`
Expected: PASS. Record the total count in the ledger.

- [ ] **Step 3: Confirm migrations are clean**

Run: `docker compose exec django python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

- [ ] **Step 4: Commit only if a fix was needed**

Commit any regression fix with a message describing it. Otherwise no commit.

---

## Self-review notes

- Spec coverage: snapshot fields (Task 1), pricing and provider rules (Task 2), all call sites and embedding/image `system_default` (Task 3), aggregation and credit debits (Task 4), superuser-only page and unknown banner (Task 5), regression (Task 6). Non-goals are not implemented.
- Types: `llm_pricing` returns `{"chat_in": P, "chat_out": P}`; `track_token_usage(pricing=P)`. Used consistently in Tasks 2–3.
- Deferred to later slices, per the spec: operation IDs, conversation and action breakdowns, trends, alerts, cached tokens, non-token costs, `estimated` source.
