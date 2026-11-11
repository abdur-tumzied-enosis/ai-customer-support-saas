# AI Cost Monitoring (Django admin) — Design

**Date:** 2026-11-10
**Source requirements:** `C:\Users\tumzied\Downloads\AI_Cost_and_Credit_Usage_Monitoring.md` (this design implements a first slice of it)
**Size:** A project. This document covers the first slice only; the rest of the source doc is deferred (see Non-goals).

## Intent

Admins need to see what AI calls actually cost the business, per model, and whether that cost is complete. Today each `UsageEvent` stores one `cost_usd` but no record of which model or rate produced it, so a later price change makes old costs impossible to explain. This slice records the rate used on every new event (a **price snapshot**) and adds an internal admin view of known cost by provider and model, next to credits charged.

## Goals (first slice)

- Every new `UsageEvent` records provider, model name, the input/output $/1M rates used, and how its cost was chosen (`cost_source`).
- Price changes never change the cost of past events.
- Unknown cost stays unknown (`cost_usd = NULL`, `cost_source = "unknown"`), never zero.
- Admin page: date range + project filter; overview totals split by cost source; per-model table (requests, tokens, known cost, unpriced count and tokens); unpriced-usage indicator.

## Non-goals (deferred)

- Operation IDs, conversation/action breakdowns, daily trends, alerts, provider-vs-calculated reconciliation.
- Cached-input token pricing (not captured today; events keep `cached_input` out of scope).
- Estimated cost source (no approved fallback estimate exists yet).
- Non-token costs (images, embeddings, messaging).
- Failed-delivery cost-to-credit conversion.
- Any change to customer-facing APIs or UI. Customers never see USD, rates, models or tokens.
- Payments, revenue, profit, or cost-coverage changes beyond the existing coverage page.
- Backfilling old events (see Migration).

## Constraints

- Keep `UsageEvent` as the source of truth for AI cost; keep `CreditTransaction` untouched.
- Use `Decimal` for all money and rates.
- `track_token_usage` must still never raise (it protects the caller's flow).
- `AIModel` remains the one price list (no new pricing table).
- Existing tests in `billing/tests/test_api.py` assert customer APIs don't leak cost fields; they must keep passing.

## Approaches considered

1. **Snapshot the rate on each event (chosen).** Old events keep their stored cost and rate forever. Small schema change; fits the existing `AIModel` table.
2. **Effective-dated price rows.** Correct, but a second pricing table and lookup-by-date logic. Rejected for now: more work than this slice needs.
3. **Recompute from current `AIModel` at report time.** Simplest, but a sync or manual price edit rewrites history. Rejected: the source doc forbids it.

## Design

### Data model (`usage.UsageEvent`, new nullable fields)

| Field | Meaning |
|---|---|
| `provider` | `google` / `openrouter` / empty for legacy and non-LLM events |
| `model_name` | Runtime model id from the response (`response_metadata["model_name"]`) |
| `price_in_per_mtok` | Input rate used, USD per 1M tokens, `Decimal(12,6)`, nullable |
| `price_out_per_mtok` | Output rate used, same type, nullable |
| `cost_source` | `provider_reported`, `token_calculated`, `unknown`; empty for legacy rows |

`cost_usd` keeps its meaning. `cost_source` is set on every new event that has an LLM call.

### Pricing lookup (`usage/utils.py`)

- `llm_costs(messages)` is replaced by one function, `llm_pricing(messages)`, that returns `(cost_in, cost_out, snapshot)` where `snapshot` holds `provider`, `model_name`, both rates, and `cost_source`. The current `llm_costs` remains as a thin wrapper that returns the first two values so existing call sites keep working.
- Match `AIModel` on **provider and name**, not name alone (the current code matches name only).
- OpenRouter reports a charged amount: use it, `cost_source = provider_reported`, and still store the matched `AIModel` rates if present (for reference). Cost is booked on `chat_out`, as today.
- Google without a matching priced `AIModel`: `cost_source = unknown`, cost `NULL`.
- Mixed messages (any unknown) mean the whole call is unknown, as today.

### Call sites

- The ~9 `track_token_usage` call sites in `knowledge`, `channels_app`, `orders`, and `agents/rag.py` pass the snapshot through a new optional `pricing` argument. `track_token_usage` writes the new fields. No other behaviour changes.

### Admin report

- New read-only admin page under the `usage` app, reachable from the admin index. Filters: start date, end date, project.
- Overview: known cost (provider-reported and token-calculated shown separately), unknown events and their token volume, total credits charged (from `CreditTransaction`, debits only).
- By-model table: provider, model, requests, input/output tokens, known cost by source, unknown count and tokens.
- A banner when any unknown events exist in the range.
- Existing coverage page (`billing/report.py`) is left as is.

### Migration

- Add nullable columns only. No data migration.
- Legacy events (before this change) have `cost_source` empty. The report groups them as "legacy, no snapshot" and never invents a rate for them.

### Access

- Page is inside the Django admin, so staff only. See open question on superusers.

## Testing

- Unit: pricing lookup (provider+name match, OpenRouter precedence, unknown path, mixed messages); snapshot stored on the event; later `AIModel` price change leaves old event unchanged.
- Integration: one call site per style (chat and background) writes the snapshot.
- Report: aggregates by model and source, unknown count, date and project filters.
- Regression: customer API FORBIDDEN fields test still passes.
- Run in Docker: `docker compose exec django python manage.py test <module> --noinput`.

## Decided by the user

- **Admin access:** superusers only. The report page requires `is_superuser`, not just `is_staff`.
- **Embeddings:** free in the MVP. Embedding events are recorded with `cost_usd = 0` and `cost_source = "system_default"` (a fourth value, used only for embeddings, so they never count as unknown). When embeddings get a price, they move to `AIModel` rates and this value stops being written.

## Decided by me (check these)

- Snapshot columns are nullable; legacy events are not backfilled.
- `AIModel` lookup matches provider and name, exact string match. Unmatched Google names stay `unknown`, and the report lists the unmatched model names so an admin can fix the `AIModel` name. No automatic normalization.
- Provider comes from the LLM instance that made the call (its configured provider), not from the message. Confirm during planning when reading the call path.
- On OpenRouter calls, the cost is booked on `chat_out` as `provider_reported`. The `chat_in` event gets `cost_usd = 0` with `cost_source = provider_reported` and a metadata note that the cost is booked on the output event. It is not `unknown`, so it does not inflate the unknown count.
- `cost_source` has `provider_reported`, `token_calculated`, `unknown`, and `system_default` (embeddings only, see above). `estimated` is not used yet.
- The report is a new page, not an extension of the coverage page.
- Reporting timezone matches the existing coverage page (Django's `TIME_ZONE`).
- Non-token costs and alerts are out of this slice.
