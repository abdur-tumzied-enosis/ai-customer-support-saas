# Credit pricing — design

Date: 2026-11-08 · Status: draft for approval

## Intent

Customers (business owners) spend **credits** on AI work. For now an admin grants credits by hand in Django admin; there is no payment method yet. The model must be the one we keep when payments arrive, so a payment later is just another way credits enter a wallet.

Customers see their balance and **how many credits each conversation used**. Admins also see the **real USD cost** behind every charge, so they can tell whether prices still cover cost and adjust them from Django admin without a deploy.

## Decisions (made by the product owner)

1. **Fixed price per action, real cost tracked.** Customers pay a fixed number of credits per visible action (e.g. 1 AI reply = 1 credit). Real USD cost (`UsageEvent.cost_usd`, already recorded) is tracked next to it for admins.
2. **Admin edits prices and markup in Django admin.** Per-action credit prices and the pricing settings (credit value, target markup) are DB rows, not code or env.
3. **Bundle background work into the visible action that caused it.** Customers are never charged for memory updates, order guards, order extraction etc. Those costs count toward the conversation's real cost only.
4. **The credit wallet belongs to a user**, not a project.
5. **Aggregated cost per conversation**: total credits per conversation (customer-facing) and total real USD (admin-facing).
6. **Catalog import is charged on extraction**: per product candidate the AI extracts, including re-runs.
7. **Team members see the paying user's balance**, banners and per-conversation credits (read-only); only the paying user sees the full Credits history page.
8. **Landing page gets a pricing section** with credit packages and feature lists that make larger packs attractive.

## Goals

- Wallet per user, with a ledger that explains every change in balance.
- Admin can grant/adjust credits, edit action prices and pricing settings in Django admin.
- Every chargeable action debits the paying user's wallet once.
- Per-conversation totals: credits (customer) and USD cost (admin).
- Admin margin report: credits charged vs real cost, flagged when below the target markup.
- Behaviour when a wallet runs out.

## Non-goals

- Payments, checkout, invoices, plans, subscriptions. Packages on the landing page are display only.
- Restricting features by package.
- Pricing photo and embedding work in USD (they stay unpriced in `cost_usd`; the margin report shows how many events are unpriced).
- Backfilling cost for conversations before launch. They show "—".
- Showing credits in the analytics page.

## Anti-goals

- A customer is charged twice for one reply (Celery retries, redelivery).
- A balance that can't be explained row by row.
- The AI going silent with no visible reason when credits run out.
- Customers seeing USD costs or our markup.

## How it works today (relevant facts)

- Every LLM call writes `usage.UsageEvent(project, kind, tokens, cost_usd, metadata)` via `usage.utils.track_token_usage`. `cost_usd` comes from OpenRouter's charged amount or `AIModel` per-Mtok prices (`usage.utils.llm_costs`). Call sites: `agents/rag.py` (reply), `channels_app/memory.py`, `orders/{guard,from_chat,improve,setup}.py`, `knowledge/{product_extract,product_suggest}.py`, `knowledge/tasks.py` (embeddings), `usage.utils.record_image_usage` (photo index/search).
- **UsageEvent has no link to a conversation or message.** Per-conversation cost needs that link.
- About 29% of chat events in local data have `cost_usd = NULL` (model with no price).
- `subscriptions.Plan` / `Subscription` exist but nothing reads them.
- Usage is recorded per **project**; a project has many members (`ProjectUser`), a user can have many projects. There is no "owner" field on `Project`.
- Replies are produced in `channels_app/reply.py::process_reply` (Celery task with retries); the AI reply is saved with `create_outbound_message(..., id=newest.agent_reply_id)`.
- Dashboard test chat uses a "self contact" (`get_or_create_self_contact`).

## Approaches considered

| | What a credit means | Trade-off | Outcome |
|---|---|---|---|
| A. Metered | credits = cost_usd × markup ÷ credit value | Never a loss, almost no code; customers can't predict spend, breaks on unknown cost, model switches change customer bills | Rejected |
| B. Per action, cost ignored | 1 reply = 1 credit | Simplest; margin erodes unnoticed | Rejected |
| **C. Per action, real cost tracked** | 1 reply = 1 credit; admin sees cost & margin | Predictable for customers, models swappable; needs price table + margin report | **Chosen** |

Wallet holder: project wallet was recommended (matches usage records); **user wallet chosen** so one owner has one balance across all their shops. Consequence: each project needs a paying user (below).

## Design

### Data model (new app `billing`; delete the unused `subscriptions` app)

**`ActionPrice`** — one row per chargeable action, edited in admin.
- `action` (unique, choices below), `credits` (Decimal, 2 dp, ≥ 0), `notes`, `updated_at`.
- `credits = 0` means free; no ledger row is written for free actions.
- Seeded by a data migration with the defaults below.

| action | Visible to customer as | Bundled hidden work | Default credits |
|---|---|---|---|
| `reply` | AI reply to a shopper | memory update, order guard, order extraction | 1 |
| `test_reply` | AI reply in dashboard test chat | same | 0 |
| `photo_search` | Shopper sends a photo, AI matches products | image embedding + lookup | 1 |
| `product_import` | One product candidate extracted from a catalog (re-runs charge again) | product extraction LLM | 2 |
| `photo_index` | One product photo indexed for search | image embedding | 0 |
| `order_setup` | Ordering settings extracted from a document | order settings LLM | 5 |
| `instruction_improve` | "Improve my instructions" button | improve LLM | 0 |
| `product_suggest` | Product suggestions | suggest LLM | 0 |

Knowledge document upload / embeddings: free, not an action.

**`PricingSettings`** — singleton (same pattern as `agents.ModelConfig`: `pk = 1`).
- `usd_per_credit` (Decimal) — what one credit is worth in money. Placeholder until payments; default `0.005`.
- `target_markup` (Decimal) — minimum acceptable `credit revenue ÷ real cost`; default `5`. Used only by the margin report.
- `overdraft_credits` (Decimal) — how far below zero shopper replies may continue; default `50`.
- `low_balance_credits` (Decimal) — show the low-balance banner below this; default `100`.

**`CreditWallet`** — `user` (OneToOne `accounts.User`), `balance` (Decimal, 2 dp, may go negative within the overdraft), `updated_at`. Created lazily on first grant/charge.

**`CreditTransaction`** — the ledger; never updated or deleted.
- `wallet` FK, `amount` (Decimal; + for grants, − for charges), `balance_after`, `kind` (`grant` | `charge` | `adjust` | `signup_bonus`), `action` (blank unless charge), `ref` (idempotency key, blank for grants), `project` FK null, `conversation` FK null, `message` FK null, `created_by` FK User null (admin who granted), `note`, `created_at`.
- Unique constraint on (`action`, `ref`) where `ref` is not blank — the guard against charging twice on Celery retry/redeliver. `ref` is the message id for `reply`/`test_reply`/`photo_search`, the `ProductCandidate` id for `product_import`, the image id for `photo_index`, and a per-call id (e.g. the Celery task id) for the owner actions. A deliberate re-run creates new candidates/ids and so is charged again (decision 6); a retry of the same unit is not.
- Wallet balance is updated in the same `transaction.atomic()` with `select_for_update()` on the wallet; `balance_after` is stored on the row.

**`Project.billing_user`** — new FK to `User` (null, PROTECT): whose wallet pays for this project. Data migration backfills it with the earliest `ProjectUser` of each project; project creation sets it to the creator. Admin can change it in Django admin.

**`UsageEvent.conversation` / `UsageEvent.message`** — new nullable FKs (SET_NULL, indexed). Every call site that runs inside a conversation passes them (reply, memory, guard, order-from-chat, photo search). This is what makes bundling work: hidden work is not charged credits, but its cost lands on the conversation.

### Charging

One function, e.g. `billing.charge(project, action, *, conversation=None, message=None) -> CreditTransaction | None`:
- Looks up `ActionPrice`; returns `None` if 0 credits.
- Debits `project.billing_user`'s wallet; writes the ledger row with the snapshot of `credits` at that moment (later price edits affect only future charges).
- Idempotent on (`action`, `ref`).
- Never raises into the caller's main work except through the explicit balance checks below.

Charge points:
- `reply` / `test_reply`: in `process_reply`, after the outbound message is saved, **only when the agent produced the reply** (not the fallback text after a failure or photo-check timeout, not a handoff message, not a dropped reply). `message` = the outbound AI message. `test_reply` when the conversation's contact is the project's self contact.
- `photo_search`: where `record_image_usage(..., "search")` is called in `channels_app/image_reply.py`, `message` = the shopper's photo message. The charge stands even if the reply then times out (the search ran). In test chat, photo search is free.
- `product_import`: once per `ProductCandidate` row created by `knowledge.product_extract.extract_candidates` (charge after each page's bulk create), whether started from the knowledge page or the order-setup wizard. Importing a reviewed candidate costs nothing more.
- `photo_index`: in `knowledge/tasks.py` next to `record_image_usage(..., "index")`.
- `order_setup`: once per successful `extract_order_settings` call (a wizard step re-run charges again). A wizard run that also extracts products pays `product_import` per candidate on top.
- `instruction_improve`, `product_suggest`: at their existing entry points.

### Running out

- **Shopper replies**: before running the agent, if `balance ≤ −overdraft_credits`, skip the agent and use the existing handoff flow with reason `"out of credits"`, so staff see it in the inbox. Photo search is skipped the same way. The conversation stays paused after a top-up, like any handoff; staff resume it as today. Test chat is never blocked.
- **Owner-triggered actions** (import, order setup, improve, suggest): the API rejects with HTTP 402 and a clear message when `balance < price of one unit`. No overdraft for these. Catalog extraction checks the balance before each page and stops cleanly with the status "stopped: out of credits" when the next page can't be paid for; candidates already extracted are kept.
- **Warnings**: dashboard banner when `balance < low_balance_credits`, a red banner at ≤ 0. One email (existing `queue_email`) when the balance first crosses 0 after a grant.

### Admin (Django admin)

- `ActionPrice`: list view with `credits` editable inline.
- `PricingSettings`: singleton form.
- `CreditWallet`: list with balance; detail shows ledger rows read-only. "Grant credits" = adding a `CreditTransaction` of kind `grant`/`adjust` with amount + note; `created_by` set to the admin; balance updated through the same function as charges.
- `Project`: `billing_user` editable.
- **Margin report** (admin page): per project for a date range — credits charged, revenue equivalent (`credits × usd_per_credit`), real cost (`Sum(UsageEvent.cost_usd)`), achieved markup, number of unpriced usage events. Rows below `target_markup` highlighted.
- Conversation admin: credits total and real USD total.

### Customer UI (Next.js dashboard)

- Header: balance of the current project's paying user (`billing_user`), shown to every member read-only; low/zero banner.
- Conversations list: "Credits" column (sum of ledger rows for that conversation), visible to all members.
- Conversation view: credits under each charged AI message (e.g. "1 credit", "2 credits · reply + photo search") and the conversation total.
- A Credits page, **paying user only**: ledger history of their wallet (grants and charges, filterable by project).
- No USD anywhere in the customer UI.

### Landing page pricing section (`frontend/app/page.tsx`)

A "Pricing" section between "Powerful Features" and "Ready to Get Started?", plus a "Pricing" link in the top nav.

**`CreditPackage`** model (in `billing`, edited in Django admin, read through a public unauthenticated `GET` endpoint so prices change without a deploy):
- `name`, `credits`, `bonus_credits`, `price` (Decimal), `currency` (default `BDT`, matching `Project.currency`), `features` (JSON list of strings), `highlighted` (bool, "Most popular" badge), `sort_order`, `active`.
- No checkout yet: every card's button reads "Get started" and goes to `/sign-up`. Under the cards: "Payments are coming soon. For now we add credits to your account. Contact us to top up."

What makes larger packs attractive: bonus credits that grow with the pack, a lower price per reply shown on each card, and a "Most popular" badge on the middle pack. Each card shows "≈ N AI replies", calculated as `(credits + bonus) ÷ ActionPrice(reply).credits`, so it stays correct when prices change.

Seed data (example prices, editable in admin):

| Pack | Credits | Bonus | Price | ≈ replies | Features |
|---|---|---|---|---|---|
| Starter | 1,000 | 0 | ৳500 | 1,000 | All channels (Messenger, WhatsApp, Instagram, web widget) · AI replies in Bangla & English · Knowledge base · Credits never expire |
| Growth (Most popular) | 5,000 | +1,000 | ৳2,000 | 6,000 | Everything in Starter · Photo search for products · Order taking from chat · Catalog import · 20% bonus credits |
| Pro | 20,000 | +6,000 | ৳7,000 | 26,000 | Everything in Growth · 30% bonus credits · Priority support · Help setting up your catalog |

Feature lists are marketing copy only. Features are **not** gated by pack: every user gets every feature, and packs differ only in credits.

A "What uses credits" strip under the cards lists the non-zero `ActionPrice` rows in customer words (e.g. "AI reply — 1 credit · Photo search — 1 credit · Catalog product — 2 credits"), using the same public endpoint.

**Sign-up bonus**: `PricingSettings.signup_credits` (default `100`, 0 disables) is granted as a `signup_bonus` ledger row when a user verifies their account, so new users can try the bot before an admin tops them up. The landing page shows "100 free credits to start" (from the same endpoint).

### API (shape; details left to the builder)

- `GET` wallet: balance, low/zero flags.
- `GET` transactions: paginated, filter by project.
- Conversation list/detail serializers gain `credits_used`; message serializer gains `credits` for charged messages.

## Left to the builder

- App naming, file layout, serializer and endpoint names.
- Admin UI details for the grant form and margin report.
- Exact frontend placement and styling, following existing dashboard components.
- How `UsageEvent` FKs are threaded through call sites (argument vs. context).
- Tests per project convention (Docker: `docker compose exec django python manage.py test ... --noinput`).
