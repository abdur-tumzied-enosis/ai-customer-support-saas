# OpenRouter Pricing Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh `AIModel` input/output prices for existing `provider="openrouter"` rows from the OpenRouter models API every day, and let an admin trigger the same sync from a button.

**Architecture:** One sync function in `agents/pricing.py` does fetch, match, and update. A Celery task wraps it and is scheduled daily in `CELERY_BEAT_SCHEDULE`. `AIModelAdmin` gets a POST-only view and a change-list button that call the same function inline.

**Tech Stack:** Django, Celery, `requests`, Django admin, Django `TestCase` with `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-11-10-openrouter-pricing-sync-design.md`

## Global Constraints

- Only rows with `provider="openrouter"` that already exist are updated; no rows are created.
- Stored prices are USD per 1M tokens, `Decimal`, 6 decimal places (`AIModel` fields).
- OpenRouter prices are USD per token strings; multiply by `1_000_000`.
- Negative or non-numeric OpenRouter prices store `None`, not `0`.
- Request: `GET https://openrouter.ai/api/v1/models`, header `Authorization: Bearer <settings.OPENROUTER_API_KEY>`, timeout 30 s, `raise_for_status()`.
- A failed fetch writes nothing (no partial updates).
- Tests run in Docker: `docker compose exec django python manage.py test <label> --noinput`.

## Review Focus

- OpenRouter returns a price of `-1` (dynamic router): stored as `None`, not `0` and not a crash. (Task 1)
- OpenRouter returns a price that is not a number (e.g. `""` or `null`): stored as `None`. (Task 1)
- A DB row whose model is missing from the OpenRouter response keeps its existing prices and is listed in `not_found`. (Task 1)
- Network or HTTP error during the admin button: the admin shows an error message, does not return a 500. (Task 3)
- The sync button is reachable only by POST and only by an admin user; GET returns 405. (Task 3)

---

### Task 1: Sync function

**Files:**
- Create: `backend/agents/pricing.py`
- Test: `backend/agents/tests/test_pricing_sync.py`

**Interfaces:**
- Consumes: `agents.models.AIModel`, `agents.models.MODEL_CONFIG_CACHE_KEY`, `settings.OPENROUTER_API_KEY`
- Produces: `sync_openrouter_prices(api_key: str | None = None, timeout: float = 30) -> dict` returning `{"updated": int, "unchanged": int, "not_found": list[str]}`; `updated` counts rows whose price changed, `unchanged` rows already at the new price, `not_found` names of openrouter rows absent from the response. Raises on HTTP/network/JSON errors and writes nothing in that case.

- [ ] **Step 1: Write the failing tests**

`backend/agents/tests/test_pricing_sync.py`:

```python
from decimal import Decimal
from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from agents.models import AIModel, ModelConfig
from agents.pricing import sync_openrouter_prices

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "pricing-sync-tests"}}


def fake_response(data):
	resp = mock.Mock()
	resp.json.return_value = {"data": data}
	return resp


@override_settings(CACHES=LOCMEM, OPENROUTER_API_KEY="test-key")
class SyncOpenRouterPricesTest(TestCase):
	def setUp(self):
		cache.clear()
		self.cheap = AIModel.objects.create(provider="openrouter", name="vendor/cheap", input_price_per_mtok=Decimal("9"), output_price_per_mtok=Decimal("9"))
		self.dynamic = AIModel.objects.create(provider="openrouter", name="vendor/router", input_price_per_mtok=Decimal("1"), output_price_per_mtok=Decimal("1"))
		self.missing = AIModel.objects.create(provider="openrouter", name="vendor/gone", input_price_per_mtok=Decimal("5"), output_price_per_mtok=Decimal("6"))
		self.google = AIModel.objects.create(provider="google", name="gemini-y", input_price_per_mtok=Decimal("2"), output_price_per_mtok=Decimal("3"))
		ModelConfig.objects.create(reply_model=self.cheap)  # warms the cache key we must clear

	@mock.patch("agents.pricing.requests.get")
	def test_updates_prices_per_million_and_reports(self, get):
		get.return_value = fake_response([
			{"id": "vendor/cheap", "pricing": {"prompt": "0.0000005", "completion": "0.0000015"}},
			{"id": "vendor/router", "pricing": {"prompt": "-1", "completion": "-1"}},
			{"id": "vendor/unrelated", "pricing": {"prompt": "0.001", "completion": "0.002"}},
		])
		result = sync_openrouter_prices()
		self.cheap.refresh_from_db()
		self.dynamic.refresh_from_db()
		self.missing.refresh_from_db()
		self.google.refresh_from_db()
		assert self.cheap.input_price_per_mtok == Decimal("0.500000")
		assert self.cheap.output_price_per_mtok == Decimal("1.500000")
		assert self.dynamic.input_price_per_mtok is None and self.dynamic.output_price_per_mtok is None
		assert self.missing.input_price_per_mtok == Decimal("5") and self.missing.output_price_per_mtok == Decimal("6")
		assert self.google.input_price_per_mtok == Decimal("2")  # not an openrouter row: untouched
		assert result == {"updated": 2, "unchanged": 0, "not_found": ["vendor/gone"]}
		assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}
		assert get.call_args.kwargs["timeout"] == 30

	@mock.patch("agents.pricing.requests.get")
	def test_second_run_reports_unchanged_and_non_numeric_price_is_none(self, get):
		get.return_value = fake_response([
			{"id": "vendor/cheap", "pricing": {"prompt": "0.0000005", "completion": "0.0000015"}},
			{"id": "vendor/router", "pricing": {"prompt": None, "completion": ""}},
		])
		sync_openrouter_prices()
		result = sync_openrouter_prices()
		assert result["unchanged"] == 2 and result["updated"] == 0
		self.dynamic.refresh_from_db()
		assert self.dynamic.input_price_per_mtok is None

	@mock.patch("agents.pricing.requests.get")
	def test_http_error_writes_nothing(self, get):
		get.side_effect = requests.ConnectionError("down")
		with self.assertRaises(requests.ConnectionError):
			sync_openrouter_prices()
		self.cheap.refresh_from_db()
		assert self.cheap.input_price_per_mtok == Decimal("9")
		assert ModelConfig.objects.get().reply_model_id == self.cheap.pk  # cache not disturbed by a failed run
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_pricing_sync --noinput`
Expected: FAIL with `ModuleNotFoundError: No module named 'agents.pricing'`

- [ ] **Step 3: Implement `sync_openrouter_prices` in `backend/agents/pricing.py`**

- `URL = "https://openrouter.ai/api/v1/models"`.
- `api_key` defaults to `settings.OPENROUTER_API_KEY`.
- Fetch with `requests.get(URL, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)`, then `raise_for_status()`, then read `resp.json()["data"]`. Let errors propagate.
- Build `prices: dict[str, tuple]` from each entry's `id` and `pricing.prompt` / `pricing.completion`. Helper `_mtok(value) -> Decimal | None`: `Decimal(str(value))` (catch `InvalidOperation`), `None` if negative, else `(d * 1_000_000).quantize(Decimal("0.000001"))`. A `None` value → `None`.
- Load `AIModel.objects.filter(provider="openrouter")`. For each row whose name is in `prices`: if the new pair equals the stored pair, count `unchanged`; else write it with `AIModel.objects.filter(pk=row.pk).update(...)` and count `updated`. Rows not in `prices` go into `not_found`.
- Wrap the writes in `transaction.atomic()`. If any row changed, `cache.delete(MODEL_CONFIG_CACHE_KEY)` once after the loop (`update()` skips `AIModel.save()`).
- Return `{"updated", "unchanged", "not_found"}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec django python manage.py test agents.tests.test_pricing_sync --noinput`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/agents/pricing.py backend/agents/tests/test_pricing_sync.py
git commit -m "feat(agents): sync OpenRouter prices into existing AI model rows"
```

### Task 2: Daily Celery task and beat schedule

**Files:**
- Modify: `backend/agents/tasks.py`
- Modify: `backend/core/settings.py` (add entry to `CELERY_BEAT_SCHEDULE`, after `propose-instructions`)
- Test: `backend/agents/tests/test_pricing_sync.py` (append a test class)

**Interfaces:**
- Consumes: `agents.pricing.sync_openrouter_prices() -> dict` (Task 1)
- Produces: Celery task `agents.tasks.sync_openrouter_prices() -> dict`; beat key `sync-openrouter-prices`.

- [ ] **Step 1: Write the failing tests**

Append to `test_pricing_sync.py`:

```python
from django.conf import settings

from agents.tasks import sync_openrouter_prices as sync_task


class SyncTaskTest(TestCase):
	def test_beat_runs_the_task_daily(self):
		entry = settings.CELERY_BEAT_SCHEDULE["sync-openrouter-prices"]
		assert entry["task"] == "agents.tasks.sync_openrouter_prices"
		assert entry["schedule"] == 86400

	@mock.patch("agents.pricing.requests.get")
	@override_settings(OPENROUTER_API_KEY="test-key")
	def test_task_returns_the_sync_summary(self, get):
		get.return_value = fake_response([])
		assert sync_task() == {"updated": 0, "unchanged": 0, "not_found": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_pricing_sync --noinput`
Expected: FAIL with `ImportError` for `sync_openrouter_prices` in `agents.tasks`

- [ ] **Step 3: Implement**

- `backend/agents/tasks.py`: add `from celery import shared_task`-decorated `sync_openrouter_prices()` that calls `pricing.sync_openrouter_prices()` (`from . import pricing`), logs the summary with `logger.info`, and returns it. Keep the existing `generate_and_send_reply` stub. Add `import logging` / `logger` if absent.
- `backend/core/settings.py`: add to `CELERY_BEAT_SCHEDULE`:
  `'sync-openrouter-prices': {'task': 'agents.tasks.sync_openrouter_prices', 'schedule': 86400,  # daily},`

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec django python manage.py test agents.tests.test_pricing_sync --noinput`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/agents/tasks.py backend/core/settings.py backend/agents/tests/test_pricing_sync.py
git commit -m "feat(agents): schedule daily OpenRouter price sync"
```

### Task 3: Admin sync button

**Files:**
- Modify: `backend/agents/admin.py` (`AIModelAdmin`)
- Create: `backend/agents/templates/admin/agents/aimodel/change_list.html`
- Test: `backend/agents/tests/test_pricing_sync.py` (append a test class)

**Interfaces:**
- Consumes: `agents.pricing.sync_openrouter_prices() -> dict` (Task 1)
- Produces: URL name `admin:agents_aimodel_sync_prices` (POST only); change-list button.

- [ ] **Step 1: Write the failing tests**

Append to `test_pricing_sync.py`. Create the superuser with `get_user_model().objects.create_superuser(...)`; check `backend/accounts/models.py` for the required fields (username vs email) before writing it.

```python
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.urls import reverse


@override_settings(CACHES=LOCMEM, OPENROUTER_API_KEY="test-key")
class SyncButtonTest(TestCase):
	def setUp(self):
		cache.clear()
		self.row = AIModel.objects.create(provider="openrouter", name="vendor/cheap")
		self.url = reverse("admin:agents_aimodel_sync_prices")
		self.client.force_login(get_user_model().objects.create_superuser(**SUPERUSER))  # SUPERUSER: the project's required fields

	def test_changelist_shows_button(self):
		resp = self.client.get(reverse("admin:agents_aimodel_changelist"))
		assert b"Sync prices from OpenRouter" in resp.content

	def test_get_is_not_allowed(self):
		assert self.client.get(self.url).status_code == 405

	@mock.patch("agents.pricing.requests.get")
	def test_post_syncs_and_redirects_with_summary(self, get):
		get.return_value = fake_response([{"id": "vendor/cheap", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}])
		resp = self.client.post(self.url, follow=True)
		self.row.refresh_from_db()
		assert self.row.input_price_per_mtok == Decimal("1.000000")
		assert any("1 updated" in str(m) for m in resp.context["messages"])

	@mock.patch("agents.pricing.requests.get")
	def test_post_shows_error_instead_of_500(self, get):
		get.side_effect = requests.ConnectionError("down")
		resp = self.client.post(self.url, follow=True)
		assert resp.status_code == 200
		assert any(m.level == messages.ERROR and "failed" in str(m) for m in resp.context["messages"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_pricing_sync --noinput`
Expected: FAIL with `NoReverseMatch` for `agents_aimodel_sync_prices`

- [ ] **Step 3: Implement**

- `backend/agents/admin.py` `AIModelAdmin`:
  - `change_list_template = "admin/agents/aimodel/change_list.html"`
  - `get_urls()` returns `[path("sync-prices/", self.admin_site.admin_view(self.sync_prices_view), name="agents_aimodel_sync_prices")] + super().get_urls()`
  - `sync_prices_view(request)`: `if request.method != "POST": return HttpResponseNotAllowed(["POST"])`. Otherwise `try: result = pricing.sync_openrouter_prices()` / `except Exception as exc: self.message_user(request, f"OpenRouter sync failed: {exc}", messages.ERROR)` / `else: self.message_user(request, f"OpenRouter prices synced: {result['updated']} updated, {result['unchanged']} unchanged, not on OpenRouter: {', '.join(result['not_found']) or 'none'}")`. Then `return redirect("admin:agents_aimodel_changelist")`.
- Template `backend/agents/templates/admin/agents/aimodel/change_list.html`:

```django
{% extends "admin/change_list.html" %}
{% block object-tools-items %}
<li>
  <form method="post" action="{% url 'admin:agents_aimodel_sync_prices' %}">
    {% csrf_token %}
    <button type="submit" class="button">Sync prices from OpenRouter</button>
  </form>
</li>
{{ block.super }}
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec django python manage.py test agents.tests.test_pricing_sync --noinput`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/agents/admin.py backend/agents/templates/admin/agents/aimodel/change_list.html backend/agents/tests/test_pricing_sync.py
git commit -m "feat(agents): admin button to sync OpenRouter prices now"
```

## Final verification

- [ ] Run the agents suite: `docker compose exec django python manage.py test agents --noinput` — Expected: all tests pass.
