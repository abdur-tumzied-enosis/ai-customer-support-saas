# Order Capture: Plan Index and Tracking

**Spec:** `docs/superpowers/specs/2026-11-04-order-capture-design.md`
**Branch for the spec:** `docs/order-capture-spec` (commit `fbd8bbd`)

The order-capture spec is split into three plans. Work them in order: **A, then B, then C**. Each plan depends on the one before it, so do not start a later plan until the earlier one is merged.

| Plan | File | Scope | Tasks | Depends on |
|---|---|---|---|---|
| **A** | [catalog and sources](2026-11-05-order-capture-a-catalog-sources.md) | Project settings and currency, product catalog, KB extraction, CSV/Excel, Google Sheet, image download, photo search filter | 16 | R2 storage (landed: `backend/core/storage.py`, commits `047f58d` to `0a4bf24`) |
| **B** | [agent ordering and Orders page](2026-11-05-order-capture-b-ordering-orders.md) | `orders` app, order instruction scope, prompt, agent tools, requirements check, setup, order settings, instruction guard, Orders page, CSV export, create from chat | 21 | Plan A |
| **C** | [actions and notifications](2026-11-05-order-capture-c-actions-notifications.md) | Custom courier actions, run engine, email, POST integration, live lookup | 12 | Plans A and B |

Each task ends with a commit. Tick the boxes as you go. A task is done when its tests pass and its commit is made.

## Progress

### Plan A: catalog and sources
- [ ] A1 Project currency and Project settings page
- [ ] A2 Product model, variants, custom fields, status constraint
- [ ] A3 Price parsing
- [ ] A4 Catalog rules (status, keys, matching, owner edits)
- [ ] A5 Catalog endpoints separated from image search
- [ ] A6 Custom product fields per KB
- [ ] A7 Variants
- [ ] A8 KB product extraction into candidates
- [ ] A9 Candidate review API and import
- [ ] A10 Image download on import
- [ ] A11 CSV and Excel source, shared row rules
- [ ] A12 Google Sheet source and scheduled sync
- [ ] A13 Products frontend
- [ ] A14 Sources and candidate review frontend
- [ ] A15 Token tracking check
- [ ] A16 Photo search returns only active products

### Plan B: agent ordering and Orders page
- [ ] B1 `orders` app, models and access helper
- [ ] B2 Order scope on custom instructions
- [ ] B3 Phone normalisation and totals
- [ ] B4 Pricing seam, drafts, submit and numbering
- [ ] B5 Built-in order prompt
- [ ] B6 Agent order tools and wiring
- [ ] B7 Order event hooks and draft expiry
- [ ] B8 Order settings API
- [ ] B9 Requirements check, go live, disable, test mode
- [ ] B10 Setup job
- [ ] B11 Existing order instructions review
- [ ] B12 Instruction guard and Improve with AI
- [ ] B13 Orders list, detail, edit, bulk status
- [ ] B14 CSV export
- [ ] B15 Create order from this chat
- [ ] B16 Permissions audit
- [ ] B17 Frontend: requirements and go live
- [ ] B18 Frontend: setup wizard
- [ ] B19 Frontend: Orders list, drawer, export
- [ ] B20 Frontend: Order settings and instructions
- [ ] B21 Frontend: create order from a conversation

### Plan C: actions and notifications
- [ ] C1 Action and run models, live-lookup setting
- [ ] C2 Outbound HTTP helper
- [ ] C3 Action template renderer
- [ ] C4 Action configuration API
- [ ] C5 Run engine (one request per order)
- [ ] C6 Order hooks: auto-run, email, POST integration
- [ ] C7 Notification settings API
- [ ] C8 Live lookup
- [ ] C9 Backend integration checks
- [ ] C10 Frontend: notifications and live lookup
- [ ] C11 Frontend: actions and run log
- [ ] C12 Final review

## Where each spec part lives

| Spec part | Covered by |
|---|---|
| 0. Project settings | A1 (page, currency, image-search move), B20 (link to Orders settings) |
| 1. Product catalog | A2, A4, A5, A6, A7, A13 |
| 2. Product sources and updates | A3, A8, A9, A10, A11, A12, A14; live lookup in C8 |
| 3. Requirements, setup, order settings | B2, B8, B9, B10, B11, B12, B17, B18, B20 |
| 4. Agent ordering behaviour | B3, B4, B5, B6, B7 |
| 5. Orders page | B1, B13, B14, B15, B16, B19, B21 |
| 6. Custom order actions | C1, C2, C3, C4, C5, C11 |
| 7. Notifications | C6, C7, C10 |
| 8. Token usage | A8, A10, A15 (catalog); B6 (replies, existing), B10, B12, B15 (orders) |

Non-goals in the spec (payments, ready-made courier integrations, customer status messages, Facebook post import, Shopify/WooCommerce, photo-first bulk add, stock counting, mobile push, fake-order detection, analytics) have no task, on purpose.

## Decisions taken in the plans

These are the spec's "Claude's call" and "left to the builder" items, plus choices the plans needed. Review them before starting. Changing any of them after a plan is underway means editing the plan first.

1. **Currency** is a field on `accounts.Project` (`currency`, default `BDT`), not a separate settings model. (A1)
2. **Status defaults to `needs_info`.** The `AddField` migration gives existing products `needs_info` as well, since none of them has a price. (A2)
3. **Variants** are a separate `ProductVariant` model. A variant without a price falls back to the product price. (A2, A7)
4. **Roles.** Members create and edit products and run actions. Admins manage custom fields, sources, imports, settings and actions. (A5, B16, C4)
5. **CSV column mapping** is heuristic only, with no LLM call. Excel support uses `openpyxl`. (A11)
6. **Google Sheet** pulls hourly (3600 s), and only from `docs.google.com`. (A12)
7. **Price cleared at the source** emails the project's admins. (A11)
8. **Photo search** returns only `active` products. (A16)
9. **New `orders` app** holds orders, settings, numbering, totals, prompt, tools and actions. (B1)
10. **Test chat** is a conversation with channel `self`. `OrderSettings.test_mode` lets test chats use the tools before go-live. Turning ordering off turns test mode off too. (B4, B9)
11. **One open draft per conversation**, enforced by a partial unique constraint. After submit the order is `new`, and `change_order(order_number)` edits it while it is still `new`. (B1, B6)
12. **Draft expiry** defaults to 48 hours, hourly beat task. Abandoned orders are hidden by default. (B7, B13)
13. **Instruction scope** is `general`, `order` or `off`. A turned-off order-taking instruction gets `off`, so nothing is deleted. (B2, B11)
14. **Instruction guard** is a deterministic floor (card, password, OTP requests) plus an LLM check using the same `memory` role as `ask_llm`. Unsafe requests are blocked. Conflicts are shown as warnings. (B12)
15. **POST-to-URL integration** reuses `ProjectWebhook`, which is one per project. All project events go to that URL, including handoff and staff-reply events. Receivers filter on the `event` field. A client who wants only order events still gets the others. This is the main trade-off in C7, and it is worth a look before release.
16. **Live lookup** caches for 300 seconds. Submit always bypasses the cache. (C8)
17. **Limits.** Line quantity 100. Order instruction length 2,000 characters. Extraction 50 pages per run, 24,000 characters per page, 30 candidates per page. Low-confidence below 0.6. (A8, A9, B3, B12)

## Known issues found while planning

These were found in the existing code during research. They are not fixed by the plans unless a task says so.

- **API-tool secrets are stored in plaintext** in `CustomInstruction.api_domains` (`backend/knowledge/models.py:302`). The spec defers encryption at rest for existing secrets. The new action headers (C4) are write-only in the API but are also stored as plaintext JSON, for the same reason.
- **Owner can read the webhook signing secret** through `_webhook_dict` (`backend/channels_app/api.py:1044`). This is existing behaviour and is left alone. Only the API-tool secrets are write-only.
- **`ipaddress.ip_address` in `agents/api_tool.py:50` sits outside any try block.** Malformed resolver output could raise instead of returning a clean error. C2 reuses that helper, so it should be checked there.
- **Webhooks import private helpers** (`_is_public_host`, `_parse_target`) from `agents/api_tool.py`. Plans A10, C2 and C8 import them the same way. Renaming them to public names is a reasonable follow-up.
- **Meta-inbox staff echoes are dropped** (`backend/channels_app/service/facebook_messing.py:99-100`). Staff replies sent from the Meta inbox do not reach the extraction for "Create order from this chat". The spec deferred this. Not in these plans.
- **Frontend has no test runner.** UI tasks are verified by `npx tsc --noEmit`, `npm run lint`, and a manual pass in the dev server. Tests are backend only.
- **Mockups referenced by the spec** (`.superpowers/brainstorm/998-1793786941/content/...`) were not checked for existence. The plans do not depend on them.

## Commit status

- Spec: committed on `docs/order-capture-spec` (`fbd8bbd`).
- Plans and this index: written, not committed.
