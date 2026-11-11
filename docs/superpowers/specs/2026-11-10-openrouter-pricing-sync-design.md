# OpenRouter pricing sync — design

Size: small change. Status: draft, awaiting approval.

## Intent

Keep the prices on our `AIModel` rows current without someone editing them by hand. A daily Celery job refreshes them, and the Django admin gets a button that runs the same sync on demand.

## Goals

- Daily refresh of `input_price_per_mtok` and `output_price_per_mtok` for `provider="openrouter"` rows.
- Admin button on the AI model list that syncs now and reports the result.

## Non-goals

- Creating new `AIModel` rows. Only models already in the table are updated (chosen by the user).
- Cache prices, `kind`, `notes`, or any other field.
- Google models. Their prices are not in the OpenRouter list.

## Anti-goals

- A failed or partial sync must not leave prices half-updated or wiped.
- A model missing from the OpenRouter response must not lose its existing prices.

## Constraints

- OpenRouter `GET https://openrouter.ai/api/v1/models` sends `Authorization: Bearer <OPENROUTER_API_KEY>` (per the docs spec). Key already in `core/settings.py`.
- `pricing.prompt` / `pricing.completion` are strings in USD per token. Stored values are USD per 1M tokens, so multiply by 1,000,000. The field allows 6 decimal places.
- `requests==2.32.5` is already a backend dependency.
- Tests run in Docker: `docker compose exec django python manage.py test ... --noinput`.

## Approaches

1. **Chosen: sync only rows already in `AIModel`.** One function does the fetch, match, and update. Beat runs it daily; the admin button calls it inline. Nothing new appears in the `ModelConfig` dropdowns.
2. Import every OpenRouter model. Rejected: several hundred rows, and `kind` would be guessed.
3. Filtered import. Rejected for now: needs a filter spec nobody has written yet.

## Design

**Sync function** — `backend/agents/pricing.py`, `sync_openrouter_prices() -> dict`:
- `GET https://openrouter.ai/api/v1/models` with the bearer key, timeout 30 s, `raise_for_status()`.
- Build `{id: (input_mtok, output_mtok)}` from `data[]`. Price → `Decimal(str) * 1_000_000`, quantized to 6 dp.
- Negative or non-numeric price (OpenRouter uses `-1` for dynamic routers) → `None` (unknown).
- Parse everything first; then, in one `transaction.atomic()`, update each matching `AIModel` with `QuerySet.update()`, then `cache.delete(MODEL_CONFIG_CACHE_KEY)` once. `update()` skips `AIModel.save()`, so the cache is cleared by hand.
- Returns `{"updated": n, "unchanged": n, "not_found": [names]}`. Names still in the table but absent from OpenRouter are left alone and listed.
- Errors propagate; no partial writes.

**Celery task** — `agents/tasks.py`: `@shared_task sync_openrouter_prices()` wraps the function, logs the summary.

**Beat** — `CELERY_BEAT_SCHEDULE['sync-openrouter-prices']`: `task: agents.tasks.sync_openrouter_prices`, `schedule: 86400` (daily, same style as `propose-instructions`).

**Admin button** — `AIModelAdmin`:
- `change_list_template` extends the default and adds a POST form button "Sync prices from OpenRouter".
- `get_urls()` adds `sync-prices/` → `admin_view(...)`, POST only; calls the sync function and uses `message_user` for success or error, then redirects to the changelist.

## Calls I made (for you to check)

- Inline call from the button, not queued, because the user wants the result right away. The sync is one HTTP request.
- Daily cadence is 86400 s from the worker start, not a fixed clock time.
- Dynamic/negative prices become `None`, not `0`.
- Cache prices and other OpenRouter pricing fields are ignored.

## Tests

`backend/agents/tests/test_pricing_sync.py`, with `requests.get` mocked:
- matching rows updated with correct per-MTok values;
- a row missing from the response keeps its old prices and is reported;
- negative price → `None`;
- HTTP error → exception, no rows changed;
- the admin POST runs the sync and redirects; GET is refused.
