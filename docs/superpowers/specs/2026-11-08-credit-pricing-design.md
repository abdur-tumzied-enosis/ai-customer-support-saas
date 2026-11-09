# Credit pricing — design

Date: 2026-11-08 · Revised: 2026-11-09 (two product reviews incorporated) · Status: approved for implementation

## 1. Intent

Customers (business owners) spend **credits** on AI work. For now an administrator grants credits by hand in Django admin; payments and checkout are not part of this release. The data model must stay suitable for payments later, so a payment becomes just another way credits enter a project wallet.

Credits belong to a **project**, not a user. Everyone with access to a project sees the same wallet balance. Changing members or who manages the business never moves, resets or otherwise touches the wallet.

Customers see the project's balance and how many credits each conversation used. Administrators also see the real USD cost behind usage, so they can judge cost coverage and change prices in Django admin without a deploy. The configured USD value per credit is an **internal estimate**, not revenue, while payments don't exist.

## 2. Product decisions

1. **Fixed price per action, real cost tracked.** Customers pay a fixed number of credits per billable action. Real USD cost (`UsageEvent.cost_usd`) is tracked separately for administrators. **There is no cost-based charging anywhere** — the earlier "charge undelivered replies by AI cost" instruction is superseded by decision 11.
2. **Prices and pricing settings live in Django admin** as DB rows, not code or env.
3. **Hidden background work is bundled into the visible action that caused it.** Memory updates, order guards, order extraction etc. are never charged separately; their cost counts toward the conversation's internal cost.
4. **The wallet belongs to a project.** A user in several projects sees an independent balance per project.
5. **Per-conversation totals**: credits for customers, known USD cost for administrators.
6. **Catalog import is charged per extracted product candidate.** A deliberate re-run is charged again; a retry of the same operation is not.
7. **All project members see the balance**, low/zero banners and per-conversation credits (read-only). **Only project admins** (`ProjectUser.role = "admin"`) see the full ledger history page.
8. **Landing page pricing section** with display-only packages; packages never gate features.
9. **Existing projects get 100 credits automatically** at rollout (every project, no eligibility filter).
10. **Partial catalog processing is allowed.** When the wallet can't pay for more, keep what was paid for and stop cleanly.
11. **An AI reply is charged only when delivered** — on the first successful send, including a later successful `redeliver`. A reply that is never delivered costs the customer nothing; its USD cost still counts toward the conversation's internal cost.
12. **Soft limits, no reservations.** Shopper replies may run to `overdraft_credits` below zero; owner actions are checked before they start and charged after they succeed, so they can take the balance below zero only by actions already running at the same moment. Catalog extraction is the exception: it never goes below zero (8.4).
13. **Signup bonus** (`signup_credits`, default 100) goes once to the **first project a user creates**, by an explicit `Project.created_by`. Projects created afterwards start at 0 until an admin grants credits.
14. **`usd_per_credit` is an internal estimate**; reports label it that way, never as revenue or profit.
15. **Dashboard test chat is always free and never blocked.** It is not a priced action.
16. **Credits never expire.** Expiry would be a new product decision.
17. **Deleting an account keeps the ledger.** A deleted project's wallet and ledger remain as an orphaned audit record.
18. **"Priority support" and "Help setting up your catalog"** stay on the Pro card and are offered to every customer who asks.
19. **Self-hosted work costs $0 per event.** Text embeddings (our `text-embeddings-inference` service) and photo embeddings (CLIP inside the worker) have no per-call bill, so their usage events record `cost_usd = 0`. Server cost is a fixed hosting cost, outside per-conversation and per-project figures.

## 3. Goals

- One wallet per project, with a ledger that explains every balance change.
- Administrators grant/adjust project credits and edit action prices and settings in Django admin.
- Each billable action is charged once; retries and redelivery are protected by an idempotency key; charges missed by a crash are recovered by a reconciliation job.
- Customers see the project balance and per-conversation credit totals.
- Administrators see per-conversation known USD cost and a project-level cost-coverage report.
- Explicit, visible behaviour when a project runs out of credits.
- Existing projects get an automatic, auditable, idempotent initial allocation.

## 4. Non-goals

- Payments, checkout, invoices, plans, subscriptions. Packages are display-only.
- Restricting features by package.
- Estimating a share of hosting cost per embedding or photo operation (decision 19).
- Backfilling cost for conversations before launch (they show `—`).
- Credits in the analytics page.
- Reservations / holds on credits.
- Snapshotting `usd_per_credit` on each charge: it is a placeholder estimate until payments, and the report always uses the current value, labelled as such. Real prices get recorded when payments arrive.
- A separate price-change history: Django admin's built-in history records who changed an `ActionPrice` / `PricingSettings` and when, and every charge row stores the credits actually debited.

## 5. Anti-goals

- Charging twice for one logical operation (Celery retry, redelivery).
- A balance that can't be explained row by row from the ledger.
- Billable work with no charge and no way to recover it.
- The AI going silent with no visible reason when credits run out.
- Customers seeing USD costs, the estimated credit value or the target ratio.
- Moving a project's credits when its members change.
- Financial history disappearing through a cascade delete.

## 6. Current system (relevant facts)

- Every LLM call writes `usage.UsageEvent(project, kind, tokens, cost_usd, metadata)` via `usage.utils.track_token_usage`; `cost_usd` comes from OpenRouter's charged amount or `AIModel` per-Mtok prices (`usage.utils.llm_costs`). Call sites: `agents/rag.py` (reply), `channels_app/memory.py`, `orders/{guard,from_chat,improve,setup}.py`, `knowledge/{product_extract,product_suggest}.py`, `knowledge/tasks.py` (embeddings), `usage.utils.record_image_usage` (photo index/search).
- `UsageEvent` has no conversation/message link yet. About 29% of chat events in local data have `cost_usd = NULL` (LLM models with no `AIModel` price).
- Image events (`record_image_usage`, `usage/utils.py:78`, covering photo index and search) and embedding events (`track_token_usage(..., "embed", ...)`, `knowledge/tasks.py:297`) never set `cost_usd`. Both run on our own servers: text embeddings via `EMBEDDINGS_URL` (`agents/vector_store.py`), photo embeddings via the local CLIP model (`agents/image_search.py`).
- `subscriptions.Plan` / `Subscription` exist but nothing reads them.
- `Project` has no creator field; `accounts/serializers.py:60` creates the creator's `ProjectUser(role="admin")`. Roles in use: `admin`, `member` (`viewer` in tests). Admin-only checks filter on `role="admin"`; `knowledge/catalog.py:126` already notifies project admins.
- Replies: `channels_app/reply.py::process_reply` (Celery, retries) saves the reply with `create_outbound_message(..., id=newest.agent_reply_id)` (`sender="bot"`), then `deliver()`. Facebook/Instagram/WhatsApp sends store `external_message_id` on success and `meta.send_error` on failure; failures queue `redeliver` (2 retries, resend only, never the LLM). `self` (test chat) and `widget` count as delivered once the row is stored. `recover_stuck_replies` already runs on Celery beat.
- `knowledge.tasks.extract_product_candidates` is a plain `@shared_task` (no retries, no `acks_late`): a crashed run is lost, never re-run. `extract_candidates` writes each page with one `bulk_create`. The order-setup wizard calls `extract_candidates` too (`orders/setup.py:184`).
- `accounts/views.py::purge_project` hard-deletes a user's solo projects (and their conversations) on account deletion.

## 7. Approaches considered

| | What a credit means | Trade-off | Outcome |
|---|---|---|---|
| A. Metered | credits from cost_usd × factor | Unpredictable for customers; breaks on unknown cost; model switches change bills | Rejected |
| B. Per action, cost ignored | 1 reply = 1 credit | Simplest; margin erodes unnoticed | Rejected |
| **C. Per action, real cost tracked** | Fixed prices; admin sees cost & coverage | Predictable; needs price table + report | **Chosen** |

Wallet holder: user wallet was first chosen, then replaced by **project wallet** — it matches how usage is already recorded and survives membership changes.

Owner-action enforcement: reservations and charge-first-then-refund were considered; **pre-check + charge after success** chosen (decision 12) as the smallest design whose worst case is a small, bounded overshoot.

## 8. Design

### 8.1 Action policy

Every billable action, its trigger, when it counts as done, and what happens without credits. An action not in this table is not billable.

| action | Default credits | Trigger | Charged when (success) | Idempotency key | Out of credits |
|---|---:|---|---|---|---|
| `reply` | 1 | shopper | the agent's reply is delivered (`process_reply` or `redeliver`) | `reply:<outbound message id>` | handoff "out of credits" before the agent runs |
| `photo_search` | 1 | shopper | the image search has run (even if the reply then times out) | `photo_search:<incoming photo message id>` | skipped with the reply (same handoff) |
| `product_import` | 2 | owner | a candidate row is saved | `product_import:<ProductCandidate id>` | 402 before start; stops mid-run, never below zero |
| `photo_index` | 0 | background | the image embedding is stored | `photo_index:<image id>` | never blocks; charged even if it takes the balance below zero |
| `order_setup` | 5 | owner | the wizard's delivery step saves the extracted settings | `order_setup:<project id>:<delivery step started_at>` | 402 before the wizard starts; the delivery step stops with `insufficient_credits` if the balance dropped meanwhile |
| `instruction_improve` | 0 | owner | the improved text is returned to the user | `instruction_improve:<uuid per request>` | 402 before start |
| `product_suggest` | 0 | owner | the column-mapping suggestion is returned (`knowledge/product_suggest.py::suggest_mapping`; nothing is saved) | `product_suggest:<uuid per request>` | 402 before start |

- A failed run is not charged; its USD cost still lands in `UsageEvent`.
- Improve and suggest run synchronously in their views with no retries, so a fresh UUID per request is the key. The order-setup step runner (`orders/setup.py::run_step`) has no retries either; a deliberate re-run sets a new `started_at` on the step, so its key changes only then. No run model is added.
- Test chat (self contact) and knowledge document upload/embeddings are free and never billable.
- Any admin may set any price, including 0. Price 0 = free: no ledger row, no balance checks, no candidate limit.

### 8.2 Data model (new app `billing`)

Removing the unused `subscriptions` app is optional cleanup; do it only after checking its migrations and references.

**`ActionPrice`** — one row per action in 8.1, edited in admin. `action` (unique), `credits` (Decimal, 2 dp, ≥ 0), `notes`, `updated_at`. Seeded by a data migration; no delete in admin. If a row is missing at runtime, the code default from 8.1 is used and an error is logged — the action is never silently free and never blocked by configuration.

**`PricingSettings`** — singleton (`pk = 1`, same pattern as `agents.ModelConfig`).
- `usd_per_credit` (Decimal, default `0.005`) — internal estimate, not revenue.
- `target_coverage_ratio` (Decimal, default `5`) — target estimated-credit-value ÷ known-cost; report only.
- `overdraft_credits` (Decimal, default `50`) — soft limit below zero for shopper replies.
- `low_balance_credits` (Decimal, default `100`) — low-balance banner threshold.
- `signup_credits` (Decimal, default `100`, `0` disables).

**`CreditWallet`** — `project` (OneToOne, **null, `SET_NULL`**), `project_name` (copied at creation, so an orphaned wallet stays identifiable), `balance` (Decimal, 2 dp), `depleted_notified` (bool, 8.5), `updated_at`. Created lazily on first grant/charge and for every existing project at rollout.

**`CreditTransaction`** — the ledger; read-only in admin, never edited or deleted.
- `wallet` FK (PROTECT), `amount` (+ grants, − charges), `balance_after`, `kind` (`grant` | `adjust` | `charge` | `initial_allocation` | `signup_bonus`), `action` (blank unless charge), `idempotency_key` (unique when not blank), `conversation` FK, `trigger_message` FK, `result_message` FK, `created_by` FK User — all four **null, `SET_NULL`** — `note`, `created_at`.
- Keys: per 8.1 for charges; `initial:<project id>`, `signup:<user id>`; blank for manual grant/adjust.
- Balance update and ledger row are written in one `transaction.atomic()` with `select_for_update()` on the wallet; `balance_after` is stored.

**`Project.created_by`** — new FK to User (null, `SET_NULL`). Set at project creation (`accounts/serializers.py`). Data migration backfills it with each project's earliest `ProjectUser` with `role="admin"`.

**`UsageEvent.conversation` / `UsageEvent.message`** — new nullable, indexed FKs (`SET_NULL`). Call sites inside a conversation pass them (reply, memory summaries and suggestion drafts, order-from-chat, photo search; `orders/guard.py` never runs inside a conversation) as explicit arguments, including across Celery boundaries. If both are given, the message must belong to the conversation.

**Self-hosted usage cost** (decision 19) — `record_image_usage` writes `cost_usd=Decimal(0)`, and the embed call in `knowledge/tasks.py` passes `cost_usd=Decimal(0)`. A data migration sets `cost_usd = 0` on existing `kind in ("image", "embed")` rows where it is NULL, so the "unpriced" count only ever means an LLM model with no price.

**Billable reply marker** — the agent's reply row gets `meta.billable = "reply"` when it is created in `process_reply`; fallback, handoff and test-chat messages don't. The reconciler (8.6) relies on it.

### 8.3 Billing service

- `billing.charge(project, action, *, key, conversation=None, trigger_message=None, result_message=None, units=1) -> CreditTransaction | None`
  - Price 0 → `None`. Key already used → returns the existing row.
  - Otherwise debits `units × price` atomically, at the price of that moment (later edits affect future charges only).
  - Always debits, even below zero: the limits are enforced by the pre-checks in 8.4, not here. Catalog extraction uses `charge_affordable` instead.
  - Database errors are logged with the key and re-raised in owner flows. In shopper flows they are logged and the reply still goes out; the reconciler (8.6) charges it later.
- `billing.charge_affordable(project, action, n, create_rows)` — catalog only (8.4).
- `billing.can_afford(project, price)` — the pre-check.
- `billing.grant(project, amount, kind, *, key="", created_by=None, note="")` — same atomic path; used by admin, rollout and signup bonus.

### 8.4 Running out

**Shopper replies and photo search**
- Before running the agent: if `balance − (price of what this turn will charge: reply, plus photo_search when the turn has a photo) < −overdraft_credits`, skip the agent and use the existing handoff flow with reason `"out of credits"`. Staff see it in the inbox; after a top-up, staff resume it as today.
- Max overshoot: replies already in flight when the limit is hit (each one turn's price).

**Owner actions** (`order_setup`, `instruction_improve`, `product_suggest`)
- Before starting: if `balance < price`, return HTTP 402 with the house error envelope `{"success": false, "error": {"code": "insufficient_credits", "message": "..."}}` (no USD; `core/middleware.py` rewrites any other error shape). Nothing runs.
- After success: `charge()` always succeeds. Max overshoot: owner actions of the same project running at the same moment.

**Catalog extraction** (`product_import`, from the knowledge page or the order-setup wizard)
- Before starting and before each page: stop if `balance < price`.
- After the LLM returns a page, in one transaction: lock the wallet, read the balance, `n = floor(balance ÷ price)` (all of them when price is 0), `bulk_create` the first `n` candidates, charge them, commit. If any were dropped, stop.
- Never goes below zero. Earlier pages are kept.
- Result shown to the owner: pages read, candidates found, saved, skipped for credits, and status `completed` or `stopped: out of credits`. Stored on a new `KnowledgeBase.last_extraction` JSON field (there is no extraction job model) and returned with the candidate list.
- `extract_product_candidates` must stay without retries/`acks_late`. If retries are ever added, keys become `product_import:<run id>:<page>:<index>` so a retried page isn't charged twice.

**Background** (`photo_index`): never blocked; charged even below zero.

### 8.5 Warnings

- Dashboard banner when `balance < low_balance_credits`; red banner when `balance ≤ 0`.
- One email (existing `queue_email`) to the project's admins when a charge takes the balance from > 0 to ≤ 0. Sent only if `wallet.depleted_notified` is false; the flag is set under the wallet lock, so concurrent charges send one email. Any grant/adjust/allocation that brings the balance above 0 clears the flag. A project with no admins gets no email: a warning is logged and the banners still show.

### 8.6 Reconciliation (Celery beat, every 10 minutes, next to `recover-stuck-replies`)

1. **Missed reply charges**: bot messages with `meta.billable = "reply"`, older than 10 minutes, delivered (`external_message_id` set, or a `widget` conversation), with no ledger row for `reply:<id>` → charge them (same key, so a race with the normal path can't double-charge). Delivered replies stay charged; never-delivered ones don't (decision 11).
2. **Balance check**: every wallet whose `balance` ≠ sum of its ledger amounts → `logger.error` with the wallet id and both values, and listed at the top of the admin cost-coverage page. Never auto-corrected; an admin fixes it with an `adjust` row.

### 8.7 Rollout and allocation

1. Deploy schema, `ActionPrice` seed, `PricingSettings`, the `Project.created_by` backfill.
2. Data migration: for every existing project, create the wallet and grant 100 credits (`kind = initial_allocation`, key `initial:<project id>`). Re-running grants nothing. The ledger filtered by kind is the audit trail.
3. Check in admin that every project has a wallet, then enable charging and enforcement.

**Signup bonus**: sign-up (`accounts/serializers.py::SignUpSerializer.create`) is the only place a project is created; it sets `created_by`. When the user verifies their email (`VerifyEmailView`, or `PasswordResetConfirmView`, which also marks them verified), grant `signup_credits` to their earliest `created_by` project with key `signup:<user id>`. Unverified accounts get nothing. The unique key stops concurrent claims. Being invited to someone else's project doesn't count. Existing projects can't get both allocations: they existed before launch, so they are never "created" afterwards; a user's later projects get nothing. A deleted-and-recreated project gets nothing (the key is per user and the ledger is kept).

### 8.8 Django admin

- `ActionPrice`: list with `credits` editable inline; no delete. Changes appear in admin history.
- `PricingSettings`: singleton form.
- `CreditWallet`: list with project/`project_name` and balance; detail shows ledger rows read-only.
- Grant / adjust: creates a `grant` / `adjust` row with amount and required note, `created_by` = admin, via `billing.grant`.
- `Project`: shows its wallet balance and `created_by` (read-only).
- **Cost-coverage report**, per project and date range: wallets failing the balance check (8.6) at the top; credits charged; estimated credit value (`credits × current usd_per_credit`, labelled "estimate"); known USD cost (`Sum(cost_usd)` of priced events); estimated coverage ratio; unpriced event count with an "incomplete" marker when > 0; breakdown by action. Rows below `target_coverage_ratio` highlighted.
- Conversation admin: credits total and known USD cost total.

### 8.9 Customer UI (Next.js dashboard)

- Header: current project's balance for every member, read-only; low/zero banners.
- Conversations list: "Credits" column (sum of ledger rows for that conversation).
- Conversation view: credits per charged message (e.g. "1 credit · reply", "1 credit · photo search") and the conversation total.
- Credits page (full ledger, filter by kind): project admins only.
- Catalog extraction result shows the saved/skipped counts from 8.4.
- No USD, estimate or ratio anywhere in the customer UI.

### 8.10 Landing page pricing (`frontend/app/page.tsx`)

A "Pricing" section between "Powerful Features" and "Ready to Get Started?", plus a "Pricing" link in the top nav.

**`CreditPackage`** (in `billing`, edited in admin, read through a public unauthenticated `GET` exposing only customer-facing fields): `name`, `credits`, `bonus_credits`, `price` (Decimal), `currency` (default `BDT`, display only), `features` (JSON list), `highlighted`, `sort_order`, `active`.

- No checkout: every button reads "Get started" → `/sign-up`. Under the cards: "Payments are coming soon. For now we add credits to your project. Contact us to top up."
- Larger packs are attractive through growing bonus credits, a lower price per reply on each card, and a "Most popular" badge on the middle pack.
- Each card shows "≈ N AI replies" = `(credits + bonus) ÷ ActionPrice(reply).credits`, hidden when the reply price is 0, with the footnote "Estimate if credits are used only for replies; other actions use credits too."

Seed data (editable):

| Pack | Credits | Bonus | Price | ≈ replies | Features |
|---|---:|---:|---:|---:|---|
| Starter | 1,000 | 0 | ৳500 | 1,000 | All channels (Messenger, WhatsApp, Instagram, web widget) · AI replies in Bangla & English · Knowledge base · Credits never expire |
| Growth (Most popular) | 5,000 | +1,000 | ৳2,000 | 6,000 | Everything in Starter · Photo search for products · Order taking from chat · Catalog import · 20% bonus credits |
| Pro | 20,000 | +6,000 | ৳7,000 | 26,000 | Everything in Growth · 30% bonus credits · Priority support · Help setting up your catalog |

Feature lists are marketing copy; packs differ only in credits (decision 18 covers the Pro service perks).

A "What uses credits" strip under the cards lists non-zero `ActionPrice` rows in customer words (e.g. "AI reply — 1 credit · Photo search — 1 credit · Catalog product — 2 credits"), plus "N free credits to start" from `signup_credits`, hidden when it is 0. All from the same public endpoint.

### 8.11 API (shape; names left to the builder)

- `GET` current project wallet: balance, low/zero flags — any project member.
- `GET` current project transactions: paginated — project admins only.
- Conversation list/detail serializers gain `credits_used`; message serializer gains its charge(s).
- Public pricing endpoint: active packages, non-zero action prices, `signup_credits`. No notes, costs, `usd_per_credit` or ratio.
- Insufficient credits: HTTP 402 with the body from 8.4.

## 9. Acceptance criteria

**Wallet and ledger**
- Each project has its own wallet; a user in two projects sees two balances. Membership changes don't touch it.
- Every balance change has an immutable ledger row; a retried key creates no second row.
- Two simultaneous charges leave balance = sum of ledger amounts.
- Changing an `ActionPrice` doesn't change existing ledger rows.
- Deleting an account keeps the wallet (project set to null, `project_name` kept) and every ledger row.
- The reconciler reports a wallet whose balance was altered outside the service, and doesn't change it.

**Allocation**
- Every existing project gets exactly 100 credits once; re-running the migration grants nothing.
- A user's first created project gets the signup bonus, also when the user was invited to another project first.
- Two simultaneous project creations by a new user grant one bonus; a second created project and a recreated project get none.

**Billing**
- A delivered reply is charged once, whether delivered by `process_reply` or `redeliver`.
- A crash after delivery but before the charge is charged by the reconciler, once.
- A never-delivered reply, fallback, handoff message or test-chat reply is not charged.
- Hidden work adds to conversation USD cost, never to credits.
- Photo search is charged when the search ran, even if the reply times out.
- A missing `ActionPrice` row charges the code default and logs an error.
- New and existing image and embed usage events have `cost_usd = 0` and are not counted as unpriced.

**Running out**
- A shopper turn that would take the balance past −`overdraft_credits` (counting reply + photo search) is handed off with "out of credits".
- An owner action with `balance < price` returns 402 and runs nothing; one that passed the check is charged after success even if the balance has since dropped.
- Catalog extraction under the wallet lock keeps only affordable candidates, never goes below zero (also with a concurrent charge during the page), and reports found/saved/skipped and `stopped: out of credits`.
- Product import at price 0 keeps every candidate and writes no ledger rows (no division by zero).
- Concurrent depleting charges send one email; a grant above 0 re-arms it; a project with no admins logs a warning instead.

**Admin and UI**
- Prices change in admin without a deploy and show in admin history.
- Grants/adjustments carry `created_by` and a note.
- Members see balance and per-conversation credits; only project admins see the ledger page.
- The report flags unpriced usage and never calls the estimate revenue.
- No customer endpoint or screen exposes USD or internal settings; "free credits" is hidden when `signup_credits` is 0.

## 10. Build order

1. `billing` models, `charge` / `charge_affordable` / `can_afford` / `grant`, with concurrency and idempotency tests — before touching any AI workflow.
2. Migrations: seed, `Project.created_by` backfill, initial allocation, $0 cost on image/embed events.
3. `UsageEvent` attribution, then charge points one action at a time.
4. Enforcement, warnings, reconciler.
5. Admin, dashboard, landing page.

## 11. Left to the builder

- App naming, file layout, serializer and endpoint names.
- Admin UI details for grant/adjust and the cost-coverage report.
- Frontend placement and styling, following existing dashboard components.
- How `UsageEvent` FKs are threaded through call sites (explicit arguments across Celery boundaries).
- The run record behind `<run id>` for owner actions, where none exists yet.
- Tests per project convention: `docker compose exec django python manage.py test ... --noinput`.

The builder may not change wallet ownership, the charging rules, balance visibility, the allocations, or partial-import behaviour without a product decision.
