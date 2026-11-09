# Credit Pricing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Frontend tasks (11–14) additionally REQUIRE the `frontend-design:frontend-design` skill before writing any UI code.

**Goal:** Project-level credit wallets with an immutable ledger, fixed per-action charges at the right points in the reply/catalog/owner flows, out-of-credit behaviour, Django admin tooling with a cost-coverage report, and the customer dashboard + landing-page pricing UI.

**Architecture:** A new `billing` Django app owns the models and one service module (`billing/service.py`) through which every balance change goes (atomic, `select_for_update`, unique idempotency key). Existing flows call the service at their success points; a Celery beat task reconciles missed reply charges and checks balances. The Next.js dashboard reads three new endpoints under `/api/billing/`.

**Tech Stack:** Django + DRF (session auth), Celery + beat, PostgreSQL, Next.js 15 App Router, React 18, Tailwind 3, shadcn/ui, lucide-react.

**Spec:** `docs/superpowers/specs/2026-11-08-credit-pricing-design.md` (read it before any task; section numbers below refer to it).

## Global Constraints

- Backend tests: `docker compose exec django python manage.py test <label> --noinput` — never a local venv. Django `TestCase` (or `TransactionTestCase` when real commits/threads matter), `APIClient` + `force_login`; no pytest.
- Match each file's existing indentation (most backend files use tabs; `knowledge/tasks.py` uses spaces).
- All credit amounts are `Decimal` with 2 dp (`max_digits=12, decimal_places=2`). Never `float`.
- Charges are negative `amount`, grants positive. `balance_after` stored on every row.
- Every balance change goes through `billing.service`; nothing else writes `CreditWallet.balance` or creates `CreditTransaction`.
- API error shape is the house envelope: `{"success": False, "error": {"code": ..., "message": ...}}`; success is `{"success": True, "data": ...}`. 402 code is exactly `insufficient_credits`.
- No USD, `usd_per_credit`, `target_coverage_ratio` or notes in any customer-facing endpoint or screen.
- Test chat = `conversation.channel == "self"`: never charged, never blocked.
- Default prices (code fallback and seed): `reply 1, photo_search 1, product_import 2, photo_index 0, order_setup 5, instruction_improve 0, product_suggest 0`.
- PricingSettings defaults: `usd_per_credit 0.005, target_coverage_ratio 5, overdraft_credits 50, low_balance_credits 100, signup_credits 100`.
- Frontend checks: `cd frontend && npx tsc --noEmit && npm run lint` (no frontend test runner exists). Visual check through the running app (`run` skill / Playwright) at 375px and 1280px, light and dark.
- Frontend copy: sentence case, no ALL-CAPS labels, no "→" on buttons, no middle-dot meta strings.

## Review Focus

1. **Fallback, handoff and dropped replies** are never charged, also when a later `redeliver` succeeds for them — test in Task 5 (`test_fallback_reply_not_charged_even_after_redeliver`).
2. **Admin sets `product_import` to 0 mid-flight** — extraction keeps every candidate, no division by zero, no ledger rows — Task 7 (`test_zero_price_keeps_all_candidates`).
3. **Account deletion** (`purge_project`) with ledger rows referencing deleted conversations/messages — ledger and wallet survive with nulls — Task 3 (`test_purge_project_keeps_ledger`).
4. **A reply that is never delivered** (redeliver gave up) is not charged by the reconciler; a widget reply is — Task 6 (`test_reconcile_skips_undelivered`, `test_reconcile_charges_widget_reply`).
5. **Concurrent charges on one wallet** keep `balance == sum(ledger)` and send one depletion email — Task 2 (`test_concurrent_charges_consistent`, `test_one_depletion_email_under_concurrency`).

---

## File map

**Create (backend):** `backend/billing/{__init__,apps,models,service,report,tasks,api,urls,admin}.py`, `backend/billing/migrations/0001_initial.py`, `0002_seed.py`, `0003_initial_allocation.py`, `backend/billing/templates/admin/billing/coverage.html`, `backend/billing/tests/{__init__,test_service,test_allocation,test_reply_billing,test_reconcile,test_catalog_billing,test_owner_actions,test_report,test_api}.py`, `backend/channels_app/admin.py`.

**Modify (backend):** `core/settings.py` (INSTALLED_APPS, beat), `core/urls.py`, `accounts/models.py` + migration (`created_by`), `accounts/serializers.py`, `accounts/views.py`, `accounts/admin.py`, `usage/models.py` + migrations, `usage/utils.py`, `agents/rag.py`, `channels_app/tasks.py`, `channels_app/memory.py`, `orders/from_chat.py`, `channels_app/reply.py`, `channels_app/image_reply.py`, `channels_app/api.py`, `knowledge/models.py` + migration (`last_extraction`), `knowledge/product_extract.py`, `knowledge/candidate_views.py`, `knowledge/tasks.py`, `knowledge/catalog_views.py`, `orders/setup.py`, `orders/api.py`.

**Create (frontend):** `frontend/lib/billing-api.ts`, `frontend/components/billing/{CreditBalance,CreditBanner,PricingSection}.tsx`, `frontend/app/(dashboard)/credits/page.tsx`.

**Modify (frontend):** `app/(dashboard)/layout.tsx`, `components/layout/Sidebar.tsx`, `components/layout/MobileSidebar.tsx`, `lib/api.ts` (types), `app/(dashboard)/conversations/page.tsx`, `app/(dashboard)/conversations/[id]/page.tsx`, `components/chat/MessageList.tsx`, `components/knowledge/catalog/CandidateReview.tsx`, `app/page.tsx`.

---

### Task 1: `billing` app, models, seed

**Files:**
- Create: `backend/billing/__init__.py`, `apps.py` (`BillingConfig`, `name="billing"`, `default_auto_field="django.db.models.BigAutoField"`), `models.py`, `migrations/0001_initial.py` (makemigrations), `migrations/0002_seed.py`
- Modify: `backend/core/settings.py` INSTALLED_APPS (add `"billing"` after `"usage"`)
- Test: `backend/billing/tests/test_service.py` (model part)

**Interfaces — Produces** (`billing/models.py`):
- `ACTIONS = {"reply": ("AI reply", Decimal("1")), "photo_search": ("Photo search", Decimal("1")), "product_import": ("Catalog product", Decimal("2")), "photo_index": ("Product photo indexed", Decimal("0")), "order_setup": ("Order setup from your documents", Decimal("5")), "instruction_improve": ("Improve instructions", Decimal("0")), "product_suggest": ("Column mapping suggestion", Decimal("0"))}` — `{action: (customer label, default credits)}`.
- `ActionPrice(action: CharField unique choices from ACTIONS, credits: Decimal, notes, updated_at auto_now)`.
- `PricingSettings` singleton exactly like `agents.ModelConfig.save` (`pk = 1`, pop `force_insert`), fields per Global Constraints; `@classmethod get() -> PricingSettings` (`get_or_create(pk=1)[0]`).
- `CreditWallet(project: OneToOneField(Project, null=True, on_delete=SET_NULL, related_name="wallet"), project_name: CharField(255), balance: Decimal default 0, depleted_notified: bool default False, updated_at auto_now)`.
- `CreditTransaction` with `KIND_CHOICES = grant, adjust, charge, initial_allocation, signup_bonus`; fields per spec 8.2: `wallet FK PROTECT related_name="transactions"`, `amount`, `balance_after`, `kind`, `action` (blank), `idempotency_key` CharField(200, blank), `conversation`/`trigger_message`/`result_message` FK to `channels_app.Conversation`/`Message` (null, SET_NULL, related_name="+"), `created_by` FK User (null, SET_NULL), `note`, `created_at`. `Meta.constraints = [UniqueConstraint(fields=["idempotency_key"], condition=~Q(idempotency_key=""), name="uniq_credit_txn_key")]`, `indexes` on `(wallet, created_at)` and `conversation`. Override `save()` to raise `ValueError` when `self.pk` already exists in DB (`not self._state.adding`) and `delete()` to raise `ValueError` — immutability.
- `CreditPackage(name, credits: PositiveIntegerField, bonus_credits: PositiveIntegerField default 0, price: Decimal(10,2), currency: CharField(3) default "BDT", features: JSONField(list), highlighted: bool, sort_order: int, active: bool default True)`, `Meta.ordering = ("sort_order",)`.
- `0002_seed.py`: `RunPython` (pattern of `agents/migrations/0002_seed_models.py`) creating one `ActionPrice` per `ACTIONS` key (`get_or_create`), the `PricingSettings` row, and the three `CreditPackage` rows from spec 8.10 (Starter/Growth highlighted/Pro, features as lists of the strings separated by "·" in the table).

- [ ] **Step 1: Write failing tests** in `test_service.py`:
  - `test_seed_has_every_action`: `set(ActionPrice.objects.values_list("action", flat=True)) == set(ACTIONS)` and `ActionPrice.objects.get(action="product_import").credits == Decimal("2")`.
  - `test_pricing_settings_singleton`: `PricingSettings(overdraft_credits=10).save()` twice → `PricingSettings.objects.count() == 1`.
  - `test_transaction_immutable`: a saved row's `.save()` and `.delete()` raise `ValueError`.
  - `test_seed_packages`: 3 active packages, exactly one `highlighted` (Growth), Pro `bonus_credits == 6000`.
- [ ] **Step 2:** `docker compose exec django python manage.py test billing --noinput` → FAIL (no module `billing`).
- [ ] **Step 3:** Implement models, `makemigrations billing`, write `0002_seed.py`.
- [ ] **Step 4:** Re-run → PASS.
- [ ] **Step 5:** Commit `feat(billing): models and seed data`.

---

### Task 2: Billing service

**Files:**
- Create: `backend/billing/service.py`
- Test: `backend/billing/tests/test_service.py`

**Interfaces — Consumes:** Task 1 models. **Produces** (`billing/service.py`):
- `class InsufficientCredits(Exception)` with `.price: Decimal`, `.balance: Decimal`.
- `price_of(action: str) -> Decimal` — `ActionPrice.credits`; on `DoesNotExist` log `logger.error` and return `ACTIONS[action][1]`.
- `wallet_for(project) -> CreditWallet` — `get_or_create(project=project, defaults={"project_name": project.name})`.
- `balance_of(project) -> Decimal` — `0` when no wallet.
- `charge(project, action, *, key: str, units: int = 1, conversation=None, trigger_message=None, result_message=None) -> CreditTransaction | None` — price 0 → `None`; existing `key` → that row; else debit `units × price` (always, even below zero). Key format is the caller's (spec 8.1 table).
- `charge_affordable(project, action, rows: list, create: Callable[[list], None]) -> int` — in one `atomic()`: lock wallet, `n = len(rows)` if price is 0 else `min(len(rows), floor(balance / price))` (0 when balance ≤ 0), call `create(rows[:n])`, write one charge row per kept row with key `f"{action}:{row.pk}"` (`bulk_create`, running `balance_after`), return `n`.
- `grant(project, amount: Decimal, kind: str, *, key: str = "", created_by=None, note: str = "") -> CreditTransaction | None` — same path; positive or negative (`adjust`) amount; existing key → `None`.
- `can_afford(project, amount: Decimal) -> bool` — `balance_of(project) >= amount`.
- `shopper_blocked(project, cost: Decimal) -> bool` — `balance_of(project) - cost < -PricingSettings.get().overdraft_credits`.
- `insufficient_credits_response(price: Decimal) -> Response` — status 402, body `{"success": False, "error": {"code": "insufficient_credits", "message": f"Not enough credits for this ({price:g} needed). Ask us to top up your project."}}`.
- Internal `_post(wallet_locked, amount, **row_fields) -> CreditTransaction` used by all three writers; it also handles the depletion flag: if old balance > 0 ≥ new balance and not `depleted_notified` → set it and `queue_email` (`accounts.tasks.queue_email`) to every `ProjectUser(role="admin")` of the project (subject `"Your {settings.PRODUCT_NAME} project is out of credits"`, body naming the project and saying shopper replies will hand off to staff); no admins → `logger.warning`. If new balance > 0 → clear the flag. Idempotency: catch `IntegrityError` on the key inside a savepoint and return the existing row.

- [ ] **Step 1: Write failing tests** (`TestCase` unless noted; create `Project`, admin `User` + `ProjectUser(role="admin")` in `setUp`):
  - `test_charge_debits_price`: `charge(p, "reply", key="reply:1")` → balance `-1.00`, row `amount=-1`, `balance_after=-1`, `kind="charge"`, `action="reply"`.
  - `test_charge_same_key_once`: two calls same key → one row, balance `-1`.
  - `test_free_action_writes_nothing`: `charge(p, "photo_index", key="photo_index:x")` → `None`, 0 rows.
  - `test_price_snapshot`: charge, change `ActionPrice` to 3, old row still `-1`.
  - `test_missing_price_row_uses_default`: delete the `reply` row → charge still `-1`, `assertLogs("billing.service", "ERROR")`.
  - `test_grant_and_balance_matches_ledger`: grant 100, charge 3 actions → `wallet.balance == wallet.transactions.aggregate(Sum("amount"))`.
  - `test_charge_affordable_partial`: balance 5, price 2, 4 rows → returns 2, `create` received 2 rows, balance `1`, two keys `product_import:<pk>`.
  - `test_charge_affordable_never_negative`: balance `-3` → returns 0, `create` called with `[]`.
  - `test_depletion_email_once_and_rearm`: grant 2, charge 1 (no mail), charge 1 → one mail (`django.core.mail.outbox` with `self.captureOnCommitCallbacks(execute=True)` and `CELERY_TASK_ALWAYS_EAGER` as in existing tests, or patch `billing.service.queue_email` and count calls), charge again → still one; grant 5 → flag cleared; charge to 0 → second mail.
  - `test_no_admins_logs_warning`: project with only a `member` → `assertLogs(level="WARNING")`, no mail.
  - `test_shopper_blocked`: overdraft 50, balance `-49`, cost 2 → `True`; cost 1 → `False`.
  - `test_insufficient_credits_response`: status 402, `data["error"]["code"] == "insufficient_credits"`.
  - `TransactionTestCase` class: `test_concurrent_charges_consistent` — 10 threads each `charge(p, "reply", key=f"reply:{i}")` (close DB connection at thread end) → balance `-10`, equals ledger sum; `test_one_depletion_email_under_concurrency` — balance 1, 5 threads charge → exactly one email queued.
- [ ] **Step 2:** Run `... test billing.tests.test_service --noinput` → FAIL.
- [ ] **Step 3:** Implement `service.py`.
- [ ] **Step 4:** Re-run → PASS.
- [ ] **Step 5:** Commit `feat(billing): atomic charge/grant service with idempotency and depletion email`.

---

### Task 3: Project creator, initial allocation, signup bonus, deletion safety

**Files:**
- Modify: `backend/accounts/models.py` (`Project.created_by = ForeignKey(User, null=True, blank=True, on_delete=SET_NULL, related_name="created_projects")`), new migration `accounts/0006_project_created_by.py` with `RunPython` backfill (earliest `ProjectUser(role="admin")` by `created_at` per project), `accounts/serializers.py:59` (`Project.objects.create(..., created_by=user)`), `accounts/views.py` (`VerifyEmailView.post` after line 119, `PasswordResetConfirmView.post` after line 174 — only when the flag flipped from False), `accounts/admin.py` (show `created_by` read-only and wallet balance in `ProjectAdmin`)
- Create: `backend/billing/migrations/0003_initial_allocation.py` (depends on `billing.0002_seed` and `accounts.0006_project_created_by`)
- Modify: `backend/billing/service.py` — add `grant_signup_bonus(user) -> CreditTransaction | None`
- Test: `backend/billing/tests/test_allocation.py`

**Interfaces — Produces:** `grant_signup_bonus(user)`: `amount = PricingSettings.get().signup_credits`; 0 → `None`; project = `Project.objects.filter(created_by=user).order_by("created_at").first()`; none → `None`; else `grant(project, amount, "signup_bonus", key=f"signup:{user.pk}")`.

`0003_initial_allocation.py`: `RunPython` over every `Project`: get_or_create wallet (`project_name`), skip if a transaction with key `initial:<project id>` exists, else create a `CreditTransaction(kind="initial_allocation", amount=100, balance_after=balance+100, idempotency_key=...)` and add to the balance. Use historical models (`apps.get_model`), not `billing.service`. Expose the body as a plain function `allocate_initial(apps, schema_editor)` so the test can call it twice.

- [ ] **Step 1: Write failing tests:**
  - `test_initial_allocation_once`: two projects; import the migration module with `importlib.import_module("billing.migrations.0003_initial_allocation")` and call `allocate_initial(django.apps.apps, None)` twice → each wallet balance `100.00`, one `initial_allocation` row each.
  - `test_signup_sets_created_by`: POST the sign-up endpoint (copy the payload from `accounts/tests/test_auth.py`) → the new project's `created_by` is the new user.
  - `test_verify_grants_signup_bonus`: sign up, verify through `VerifyEmailView` (patch `check_verification_code` to `True`) → project balance `100`, kind `signup_bonus`; verify again path (call `grant_signup_bonus` again) → still one row.
  - `test_invited_member_still_gets_bonus`: user is first a `member` of another project, then has their own `created_by` project → `grant_signup_bonus` grants to their own project.
  - `test_signup_bonus_disabled`: `signup_credits = 0` → no row.
  - `test_existing_verified_user_no_bonus`: a pre-existing user with `email_verified=True` never passes through verify → no `signup_bonus` rows after allocation.
  - `test_purge_project_keeps_ledger`: wallet with a charge row pointing at a conversation and message → `accounts.views.purge_project(project)` → wallet exists with `project=None`, `project_name` kept, row exists with `conversation=None`.
- [ ] **Step 2:** Run `... test billing.tests.test_allocation --noinput` → FAIL.
- [ ] **Step 3:** Implement model field, migrations, serializer/view hooks, `grant_signup_bonus`, admin fields.
- [ ] **Step 4:** Re-run, then the accounts suite: `... test accounts billing --noinput` → PASS.
- [ ] **Step 5:** Commit `feat(billing): project creator, initial 100-credit allocation, signup bonus`.

---

### Task 4: Usage attribution and $0 self-hosted cost

**Files:**
- Modify: `backend/usage/models.py` (`UsageEvent.conversation = ForeignKey("channels_app.Conversation", null=True, blank=True, on_delete=SET_NULL, related_name="usage_events", db_index=True)`, `UsageEvent.message = ForeignKey("channels_app.Message", ... related_name="usage_events")`), migrations `usage/0004_usageevent_conversation_message.py` and `usage/0005_self_hosted_cost_zero.py` (`UsageEvent.objects.filter(kind__in=["image", "embed"], cost_usd__isnull=True).update(cost_usd=0)`)
- Modify: `backend/usage/utils.py` — `track_token_usage(..., cost_usd=None, conversation_id=None, message_id=None)`; `record_image_usage(project_id, op, *, conversation_id=None, message_id=None, **metadata)` writing `cost_usd=Decimal(0)`. If both ids given and the message's `conversation_id` differs → log warning and drop `message_id`.
- Modify: `backend/knowledge/tasks.py:297` — pass `cost_usd=Decimal(0)`.
- Modify: `backend/agents/rag.py` — `Context` gains `conversation_id: Optional[str] = None`, `message_id: Optional[str] = None`; `_track_token_usage` passes them from the context (thread `context` in, don't change `generate_reply`'s public signature — tests patch it).
- Modify: `backend/channels_app/tasks.py:133` — after `build_reply_context`, set `context.conversation_id = str(msg.conversation_id) if msg.conversation_id else None`, `context.message_id = str(msg.id)`.
- Modify: `backend/channels_app/memory.py` — `ask_llm(project_id, system, user, schema, conversation_id=None)`; pass it from `summarize_episode` (`conversation.id`) and `draft_knowledge_suggestion` (`staff_msg.conversation_id`).
- Modify: `backend/orders/from_chat.py` — `_ask(..., conversation_id=None)` from `prefill_from_conversation(conversation)`.
- Modify: `backend/channels_app/image_reply.py:38` — `record_image_usage(..., conversation_id=str(conversation.id), message_id=str(msg.id), ...)`.
- Test: extend `backend/usage/tests/test_image_usage.py`; new `backend/usage/tests/test_attribution.py`

- [ ] **Step 1: Write failing tests:**
  - `test_image_usage_cost_zero`: `record_image_usage(pid, "index")` → `cost_usd == Decimal(0)`.
  - `test_embed_cost_zero_backfill`: create an `embed` row with `cost_usd=None`, call the `0005` migration function → `0`.
  - `test_track_token_usage_links_conversation`: pass `conversation_id`, `message_id` → FKs set.
  - `test_mismatched_message_dropped`: message from another conversation → `message` is `None`, warning logged.
  - `test_reply_usage_attributed`: run the reply pipeline the way `channels_app/tests/test_reply_pipeline.py` does, but patch `agents.rag`'s LLM layer only (or call `generate_agent_reply` with a fake LLM) so `_track_token_usage` runs → the `chat_out` event has the conversation. If patching that deep is impractical, assert on `Context` instead: `build_reply_context` + the `generate_agent_reply` assignment produce `context.conversation_id == str(conv.id)`.
- [ ] **Step 2:** Run `... test usage --noinput` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `... test usage channels_app orders knowledge --noinput` → PASS (no regressions from the new kwargs).
- [ ] **Step 5:** Commit `feat(usage): conversation/message attribution, $0 cost for self-hosted embeddings`.

---

### Task 5: Reply and photo-search charging, out-of-credits handoff

**Files:**
- Modify: `backend/channels_app/reply.py` (`_reply`, `redeliver`)
- Modify: `backend/channels_app/image_reply.py` (`_photo_note`)
- Modify: `backend/billing/service.py` — add `charge_delivered_reply(out_msg) -> CreditTransaction | None`
- Test: `backend/billing/tests/test_reply_billing.py` (reuse `PipelineBase` from `channels_app/tests/test_reply_pipeline.py` by import)

**Interfaces — Produces:**
- `charge_delivered_reply(out_msg)`: returns `None` unless `(out_msg.meta or {}).get("billable") == "reply"`; else `charge(out_msg.project, "reply", key=f"reply:{out_msg.id}", conversation=out_msg.conversation, trigger_message=Message.objects.filter(id=out_msg.meta.get("trigger_id")).first(), result_message=out_msg)`. Wrap in `try/except Exception: logger.exception(...)` — a billing failure never blocks a shopper reply (the reconciler recovers it).

**Changes in `_reply`:**
1. After the `is_paused()` early return and **before** `image_reply.annotate_turn`: if `conversation.channel != "self"`, compute `cost = price_of("reply") + (price_of("photo_search") if any((m.meta or {}).get("photo_reply") for m in turn) else 0)`; if `shopper_blocked(conversation.project, cost)` → set `out_of_credits = True` and skip `annotate_turn`.
2. In the `if/elif` chain add, first: `if out_of_credits: reply_text = ""; handoff_reason = "out of credits"` (then the existing handoff path runs `start_handoff` and sends the handoff message).
3. Track `agent_replied = bool(reply_text)` immediately after a successful `generate_agent_reply` and before the handoff/fallback overrides; billable iff `agent_replied and not handoff_reason and conversation.channel != "self"`.
4. In the `create_outbound_message` meta add `**({"billable": "reply", "trigger_id": str(newest.id)} if billable else {})`. (`create_outbound_message` has no `reply_to_message` parameter, so the trigger travels in meta.)
5. After `deliver(...)` returns `True` → `charge_delivered_reply(out_msg)`. In `redeliver`, after `out_msg.status = "completed"` save → `charge_delivered_reply(out_msg)`.

**Changes in `_photo_note`:** after `record_image_usage(...)`, if `conversation.channel != "self"` → `charge(conversation.project, "photo_search", key=f"photo_search:{msg.id}", conversation=conversation, trigger_message=msg)` inside `try/except Exception: logger.exception` (one charge per photo message even with 3 photos — the key dedupes).

- [ ] **Step 1: Write failing tests** (grant 100 in `setUp`):
  - `test_delivered_reply_charged_once`: run `process_reply` → one `reply` row, `result_message` = the bot message, `trigger_message` = the inbound; run the task again (redelivery of the same message id path) → still one.
  - `test_failed_then_redelivered_charged_once`: `send_text_message` returns `{}` first (no `message_id`) → no row; call `redeliver.apply(args=[out_id])` with the patch now succeeding → one row.
  - `test_never_delivered_not_charged`: sends always fail; `redeliver` exhausts retries → zero rows.
  - `test_fallback_reply_not_charged_even_after_redeliver`: `generate_reply` raises with retries exhausted → fallback sent → no `billable` in meta, zero rows; same after a forced `redeliver`.
  - `test_handoff_reply_not_charged`: customer text "I want a human" (whatever `wants_human` matches in `channels_app/handoff.py`) → zero rows.
  - `test_test_chat_free_and_never_blocked`: self-contact conversation with balance `-1000` → agent runs (patched `generate_reply` called), zero rows.
  - `test_out_of_credits_hands_off`: balance set to `-50` via `grant(..., "adjust")` → `generate_reply` not called, conversation `handoff_reason == "out of credits"`, no charge.
  - `test_overdraft_counts_photo`: balance `-49`, turn with `photo_reply` → handed off (cost 2); same without photo → agent runs.
  - `test_photo_search_charged_even_if_reply_times_out`: patch `search_products`/`embed_image`/`fetch_photo` to succeed and `generate_agent_reply` to raise `SoftTimeLimitExceeded` → one `photo_search` row, no `reply` row.
- [ ] **Step 2:** Run `... test billing.tests.test_reply_billing --noinput` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `... test billing channels_app --noinput` → PASS.
- [ ] **Step 5:** Commit `feat(billing): charge delivered replies and photo searches; hand off when out of credits`.

---

### Task 6: Reconciler

**Files:**
- Create: `backend/billing/tasks.py`
- Modify: `backend/core/settings.py` CELERY_BEAT_SCHEDULE: `'billing-reconcile': {'task': 'billing.tasks.reconcile', 'schedule': 600}`
- Test: `backend/billing/tests/test_reconcile.py`

**Interfaces — Produces:**
- `@shared_task reconcile() -> None` = `charge_missed_replies()` then `check_balances()`.
- `charge_missed_replies() -> int`: `Message.objects.filter(direction="out", sender="bot", meta__billable="reply", created_at__lt=now-10min, created_at__gte=now-7days).filter(Q(~Q(external_message_id="")) | Q(contact__platform="widget"))`, excluding ids whose key `reply:<id>` exists (`CreditTransaction.objects.filter(idempotency_key__in=[...])`); call `charge_delivered_reply` on each; return count. (7-day window bounds the scan.)
- `check_balances() -> list[CreditWallet]`: wallets where `balance != Coalesce(Sum("transactions__amount"), 0)`; `logger.error` per wallet with id, stored, ledger sum; return them (the admin report reuses this).

- [ ] **Step 1: Write failing tests:**
  - `test_reconcile_charges_delivered_uncharged`: bot message with `billable`, `external_message_id="mid"`, `created_at` 20 min ago, no row → one row after `reconcile()`; second run → still one.
  - `test_reconcile_skips_undelivered`: same but `external_message_id=""`, facebook contact → zero rows.
  - `test_reconcile_charges_widget_reply`: widget contact, no external id → one row.
  - `test_reconcile_skips_recent`: created 2 min ago → zero rows.
  - `test_check_balances_reports_mismatch`: `CreditWallet.objects.filter(pk=w.pk).update(balance=999)` → returned list contains it, `assertLogs(level="ERROR")`, balance still 999.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `... test billing --noinput` → PASS.
- [ ] **Step 5:** Commit `feat(billing): reconcile missed reply charges and ledger/balance drift`.

---

### Task 7: Catalog extraction charging and partial runs

**Files:**
- Modify: `backend/knowledge/models.py` (`KnowledgeBase.last_extraction = JSONField(default=dict, blank=True)`) + migration
- Modify: `backend/knowledge/product_extract.py::extract_candidates`
- Modify: `backend/knowledge/candidate_views.py` (`product_extract` view pre-check; `candidate_list` response adds `"last_extraction": kb.last_extraction`)
- Modify: `backend/orders/setup.py::_products`
- Test: `backend/billing/tests/test_catalog_billing.py` (fake LLM per `orders/tests/test_setup.py` `FakeLLM`, or patch `knowledge.product_extract._ask` to return an object whose `.products` yields N items)

**Interfaces:**
- `extract_candidates` keeps its signature and return value (`int` = saved count). Changes:
  - Before each page: `price = price_of("product_import")`; if `price > 0 and not can_afford(kb.project, price)` → `stopped = True; break`.
  - Replace `ProductCandidate.objects.bulk_create(rows)` with `saved = charge_affordable(kb.project, "product_import", rows, ProductCandidate.objects.bulk_create)`; `found += len(rows)`; `skipped += len(rows) - saved`; if `saved < len(rows)` → `stopped = True` and break after this page.
  - At the end write `kb.last_extraction = {"status": "stopped: out of credits" if stopped else "completed", "pages": attempted, "found": found, "saved": created, "skipped": skipped, "finished_at": timezone.now().isoformat()}` and `kb.save(update_fields=["last_extraction"])`. Keep the existing "all pages failed" `RuntimeError`.
  - Do **not** add retries or `acks_late` to `extract_product_candidates` — add the one-line comment `# no retries: a retried page would make new candidate ids and charge again (spec 8.4)`.
- `product_extract` view: after `_kb_for`, `price = price_of("product_import")`; `if price and not can_afford(kb.project, price): return insufficient_credits_response(price)`.
- `_products` step: return message `f"Found {count} products to review"` plus `f", {kb.last_extraction['skipped']} skipped: out of credits"` when status is stopped.

- [ ] **Step 1: Write failing tests:**
  - `test_candidates_charged_per_row`: balance 100, 2 pages × 3 products → 6 candidates, 6 rows with `product_import:<candidate id>`, balance 88, `last_extraction["status"] == "completed"`.
  - `test_partial_page_keeps_affordable`: balance 5, page of 4 → 2 saved, 2 skipped, balance 1, status `stopped: out of credits`, second page never asked (`_ask` called once).
  - `test_concurrent_drop_during_page`: patch `_ask` so that while it runs, another `charge` takes 4 credits (side effect inside the fake) → saved count uses the post-drop balance, balance never < 0.
  - `test_zero_price_keeps_all_candidates`: price 0, balance `-10` → all saved, zero ledger rows.
  - `test_extract_view_402`: balance 1 → POST `/api/kb/products/extract/` → 402, `error.code == "insufficient_credits"`, task not queued (patch `.delay`).
  - `test_rerun_charges_again`: run twice (order-setup `_products` deletes pending candidates first) → second run creates new rows and new charges.
  - `test_candidate_list_includes_last_extraction`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `... test billing knowledge orders --noinput` → PASS.
- [ ] **Step 5:** Commit `feat(billing): per-candidate catalog charges with partial stop`.

---

### Task 8: Owner actions and photo index

**Files:**
- Modify: `backend/orders/api.py` — `SetupRunView.post` (before `run_setup`) and `InstructionImproveView.post`
- Modify: `backend/orders/setup.py::_delivery`
- Modify: `backend/knowledge/catalog_views.py::MappingSuggestView.post`
- Modify: `backend/knowledge/tasks.py::embed_product_image`
- Test: `backend/billing/tests/test_owner_actions.py`

**Changes:**
- `SetupRunView`: `price = price_of("order_setup")`; if `price and not can_afford(project, price)` → `insufficient_credits_response(price)`.
- `_delivery(project_id, kb_id, beat)`: before `extract_order_settings`, if the price is non-zero and the balance can't cover it → `return "needs_input", "Not enough credits to read your delivery and payment settings. Ask us to top up your project.", "insufficient_credits"`. After success: read the step's `started_at` from `OrderSettings.setup_steps` (key `"delivery"`) and `charge(project, "order_setup", key=f"order_setup:{project_id}:{started_at}")`.
- `InstructionImproveView`: pre-check `instruction_improve` price (402); after `improve(...)` returns → `charge(project, "instruction_improve", key=f"instruction_improve:{uuid4()}")`. No charge on `NoSafeSuggestion`.
- `MappingSuggestView`: same pattern with `product_suggest` after `suggest_mapping` returns; no charge on its 503 path.
- `embed_product_image`: after `record_image_usage(..., "index", ...)` → `charge(kb.project, "photo_index", key=f"photo_index:{image_id}")` (never blocked).

- [ ] **Step 1: Write failing tests** (set prices non-zero in each test where default is 0):
  - `test_setup_run_402_when_broke`: balance 4 → 402, `run_setup` not called (patch `orders.api.run_setup`).
  - `test_delivery_step_charges_once`: run `_delivery` with patched `extract_order_settings` → one row keyed with the step's `started_at`; run again with the same `started_at` → still one; new `started_at` → two.
  - `test_delivery_step_needs_input_when_broke`: balance 0 at step time → returns `("needs_input", ..., "insufficient_credits")`, extractor not called.
  - `test_improve_charged_on_success_only`: price 1; success → one row; `NoSafeSuggestion` → none; balance 0 → 402.
  - `test_suggest_charged_on_success_only`: price 1; same three cases on `/api/kb/products/sources/suggest-mapping/`.
  - `test_owner_action_after_check_can_go_negative`: balance 5, pre-check passes, another charge drops it to 2 before the action finishes → action charged, balance `-3` (soft rule, decision 12).
  - `test_photo_index_charged_even_below_zero`: price 1, balance 0 → indexed and balance `-1`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `... test billing orders knowledge --noinput` → PASS.
- [ ] **Step 5:** Commit `feat(billing): order setup, improve, suggest and photo index charging`.

---

### Task 9: Django admin and cost-coverage report

**Files:**
- Create: `backend/billing/admin.py`, `backend/billing/report.py`, `backend/billing/templates/admin/billing/coverage.html`, `backend/channels_app/admin.py`
- Test: `backend/billing/tests/test_report.py`

**Interfaces — Produces** (`billing/report.py`):
- `coverage(start: date, end: date) -> dict` → `{"mismatched": [wallet...] (from check_balances), "rows": [ {project_id, project_name, credits_charged: Decimal, estimated_value_usd: Decimal, known_cost_usd: Decimal, unpriced_events: int, ratio: Decimal | None, below_target: bool, by_action: {action: credits}} ], "usd_per_credit": Decimal, "target": Decimal}`. `credits_charged` = `-Sum(amount)` of `kind="charge"` rows in range; `known_cost_usd` = `Sum(cost_usd)` of `UsageEvent` in range where not null; `unpriced_events` = count where null; `ratio = estimated/known` (None when known is 0); `below_target = ratio is not None and ratio < target`.
- `conversation_totals(conversation) -> tuple[Decimal, Decimal]` → (credits, known USD).

**Admin:**
- `ActionPriceAdmin`: `list_display=("action", "credits", "updated_at")`, `list_editable=("credits",)`, no add/delete permission.
- `PricingSettingsAdmin`: singleton exactly like `agents/admin.py::ModelConfigAdmin`.
- `CreditPackageAdmin`: `list_display` name/credits/bonus/price/highlighted/active, `list_editable` sort_order/active.
- `CreditWalletAdmin`: `list_display=("project_name", "project", "balance", "updated_at")`, search by project name, all fields read-only, `CreditTransactionInline` (read-only, no add/delete, newest first). `get_urls()` adds `coverage/` → `admin_site.admin_view(self.coverage_view)` rendering `coverage.html` with `report.coverage(start, end)` from `?start=&end=` (default: last 30 days). Template extends `admin/base_site.html`: mismatch list on top, a table with the row fields, a "Some usage has no known cost; figures are incomplete" note when any `unpriced_events > 0`, row highlighting for `below_target`, column header "Estimated credit value (USD)" — never "revenue".
- `CreditTransactionAdmin`: list read-only (`has_change_permission` False, `has_delete_permission` False); add form limited to `wallet`, `kind` (choices `grant`, `adjust`), `amount`, `note` (required); `save_model` calls `service.grant(obj.wallet.project, obj.amount, obj.kind, created_by=request.user, note=obj.note)` instead of saving (reject a wallet with `project=None`).
- `channels_app/admin.py`: register `Conversation` with `list_display=("id", "project", "name", "credits", "known_cost")` where both columns come from `conversation_totals`, `list_select_related=("project",)`, read-only.

- [ ] **Step 1: Write failing tests** for `report.coverage`: two projects with charges and usage events (one with `cost_usd=None`) → expected sums, `unpriced_events == 1`, `ratio` computed, `below_target` true for a project whose ratio < 5; date filter excludes outside rows; `conversation_totals` sums only that conversation. Plus `test_admin_pages_load`: superuser `force_login`, GET changelists for every registered billing model and the `coverage/` URL → 200; POST the CreditTransaction add form → wallet balance changes and row has `created_by`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `... test billing --noinput` → PASS.
- [ ] **Step 5:** Commit `feat(billing): admin, grants, cost-coverage report`.

---

### Task 10: Customer API

**Files:**
- Create: `backend/billing/api.py`, `backend/billing/urls.py`
- Modify: `backend/core/urls.py` (`path("api/billing/", include("billing.urls"))`), `backend/channels_app/api.py` (`ConversationListView.get`, `_message_dict` + messages GET)
- Test: `backend/billing/tests/test_api.py`

**Endpoints** (use `orders.api._scoped`/`_err`/`_ok` style helpers or copy them; `project_id` query param):
- `GET /api/billing/wallet/?project_id=` (any member) → `{"balance": "12.00", "low": bool, "empty": bool}`; `low = balance < low_balance_credits`, `empty = balance <= 0`.
- `GET /api/billing/transactions/?project_id=&kind=&offset=0&limit=50` (admin only; limit capped at 100) → `{"results": [{id, created_at, kind, action, action_label, amount, balance_after, note, conversation_id}], "count": int}`. `note` is shown only for `grant`/`adjust`/`signup_bonus`/`initial_allocation` (admin-written text for the customer).
- `GET /api/billing/pricing/` (`AllowAny`, `authentication_classes=[]`) → `{"packages": [{name, credits, bonus_credits, price, currency, features, highlighted}], "actions": [{action, label, credits}] (non-zero only, in ACTIONS order), "signup_credits": "100.00"}`.
- Conversations list: annotate `credits_used` with `Subquery(CreditTransaction.objects.filter(conversation=OuterRef("pk"), kind="charge").values("conversation").annotate(s=Sum("amount")).values("s"))`, emitted as `str(-(value or 0))` on each item.
- Messages GET: one query for the page's charge rows (`conversation=conv, kind="charge"`), map `result_message_id → reply credits` and `trigger_message_id → photo_search credits`; `_message_dict` gains `"credits": [{"action": "reply", "label": "AI reply", "credits": "1.00"}]` (empty list when none). Conversation detail GET gains `"credits_used"`.

- [ ] **Step 1: Write failing tests:** member gets wallet (and `low`/`empty` flags at 150/50/0 balances); non-member → 404; member → 403 on transactions; admin sees rows, `kind` filter works, `limit=500` returns ≤100; pricing endpoint works logged out, contains no `notes`/`usd_per_credit`/`target` keys anywhere in the JSON string, omits zero-price actions; conversation list item has `credits_used == "2.00"` after reply + photo charges; message dicts carry the right `credits` entries.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `... test billing channels_app --noinput` → PASS.
- [ ] **Step 5:** Commit `feat(billing): wallet, ledger and public pricing endpoints; credits on conversations`.

---

### Frontend design brief (Tasks 11–14)

Load `frontend-design:frontend-design` before each frontend task. Dashboard screens (Tasks 11–13) must look native to the existing app: shadcn `new-york`, neutral tokens in `app/globals.css`, Inter, `Alert`/`Badge`/`Card`/`Table` from `components/ui`. Spend design boldness only on the landing pricing section (Task 14), and even there stay on the site's tokens (neutral palette, Inter, `--radius: 0.5rem`) so it doesn't look pasted in.

Credit numbers: show whole numbers without decimals (`Number(x).toLocaleString()`; keep up to 2 decimals only when the value has them). Negative balances show with a minus sign, not parentheses.

---

### Task 11: Balance in the header, banners, Credits nav and page

**Files:**
- Create: `frontend/lib/billing-api.ts`, `frontend/components/billing/CreditBalance.tsx`, `frontend/components/billing/CreditBanner.tsx`, `frontend/app/(dashboard)/credits/page.tsx`
- Modify: `frontend/app/(dashboard)/layout.tsx` (add `useAuth()`; render `<CreditBalance />` before `<ThemeToggle />` at line ~77; `<CreditBanner />` between `</header>` and the content div at line ~117), `frontend/components/layout/Sidebar.tsx:23-31` and `MobileSidebar.tsx:19-27` (add `{ href: "/credits", label: "Credits", icon: Coins }` rendered only when `projects[0]?.role === "admin"`; MobileSidebar gains `useAuth()`)

**Interfaces — Produces** (`lib/billing-api.ts`, using `apiFetch` from `lib/api.ts`):
```ts
export type Wallet = { balance: string; low: boolean; empty: boolean };
export type CreditTxn = { id: number; created_at: string; kind: "grant" | "adjust" | "charge" | "initial_allocation" | "signup_bonus"; action: string; action_label: string; amount: string; balance_after: string; note: string; conversation_id: string | null };
export type PublicPricing = { packages: { name: string; credits: number; bonus_credits: number; price: string; currency: string; features: string[]; highlighted: boolean }[]; actions: { action: string; label: string; credits: string }[]; signup_credits: string };
export const billingApi = {
  wallet: (projectId: string) => apiFetch<Wallet>(`/api/billing/wallet/?project_id=${projectId}`),
  transactions: (projectId: string, p: { kind?: string; offset?: number; limit?: number }) => apiFetch<{ results: CreditTxn[]; count: number }>(...),
  pricing: () => apiFetch<PublicPricing>("/api/billing/pricing/"),
};
export function formatCredits(value: string | number): string;
```
- `CreditBalance`: fetch on mount and whenever `usePathname()` changes; renders `"{n} credits"` as a `Badge` (variant `outline`; `destructive` when `empty`); links to `/credits` for admins, plain text for others; hidden while loading or on error.
- `CreditBanner`: shared with `CreditBalance` through a small context or by fetching once in the layout and passing props (pick props — simpler). `empty` → `Alert variant="destructive"`: "Your project is out of credits. Shoppers are being handed to your team instead of the AI. Contact us to top up." `low` (and not empty) → default `Alert`: "Your project has {n} credits left. Contact us to top up before the AI stops replying."
- Credits page: admin-only (non-admins see "Only project admins can see credit history."); balance summary at top; a `Table` with Date, What happened (`action_label` for charges, otherwise "Credits added" / "Adjustment" / "Starting credits" / "Welcome credits" + note), Credits (signed, green for positive), Balance; kind filter (`Select`: All, Charges, Credits added); "Load more" button (offset paging, 50 per page); conversation rows link to `/conversations/{id}`. Empty state: "No credit activity yet."

- [ ] **Step 1:** Invoke `frontend-design:frontend-design`; sketch the header badge + banner + table against existing screens.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit && npm run lint` → no errors.
- [ ] **Step 4:** Run the app, sign in as admin and as member; check badge, both banners (set balance via Django admin to 50 and to 0), nav item visibility, Credits page at 375px and 1280px, light and dark.
- [ ] **Step 5:** Commit `feat(frontend): credit balance, banners and credits page`.

---

### Task 12: Credits on conversations and messages

**Files:**
- Modify: `frontend/lib/api.ts` (`ConversationListItem` gains `credits_used: string`; `ConversationMessage` gains `credits: { action: string; label: string; credits: string }[]`), `frontend/app/(dashboard)/conversations/page.tsx:184-189` (append `• {formatCredits(conv.credits_used)} credits` when non-zero), `frontend/app/(dashboard)/conversations/[id]/page.tsx` (`toChatMessage` maps `credits`; header shows the conversation total from detail `credits_used`; after a polled agent reply arrives, refetch that message page so its credits show), `frontend/components/chat/MessageList.tsx` (`ChatMessage` gains `credits?: {label: string; credits: string}[]`; render below the bubble next to `photoMatches` as `text-xs text-muted-foreground`, e.g. "1 credit, AI reply" — one line per entry, singular/plural handled)

- [ ] **Step 1:** Invoke `frontend-design:frontend-design`; keep labels quiet (muted, xs) so they don't compete with message text.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** `npx tsc --noEmit && npm run lint` → clean.
- [ ] **Step 4:** In the running app, open a channel conversation with charges and a test-chat conversation (no labels, no total); check mobile width.
- [ ] **Step 5:** Commit `feat(frontend): credits per conversation and per message`.

---

### Task 13: Catalog extraction result and insufficient-credit errors

**Files:**
- Modify: `frontend/lib/api.ts` (candidate list response type gains `last_extraction: { status: string; pages: number; found: number; saved: number; skipped: number; finished_at: string } | {}`), `frontend/components/knowledge/catalog/CandidateReview.tsx` (+ the loader in `ProductsSection.tsx:56-63` / `SetupStepProducts.tsx` that fetches candidates — pass `lastExtraction` down)

- Show, above the candidate list, when `last_extraction.status === "stopped: out of credits"`: `Alert` "We stopped after {saved} of {found} products: your project ran out of credits. Top up, then run extraction again to get the rest." When completed: muted line "Last run read {pages} pages and found {saved} products."
- 402s: the backend message already reads as a sentence and `formatError` shows `err.message` when there are no `details`, so existing toasts (`CandidateReview.tsx:43-52`, `OrderInstructions.tsx:56-67`, `ProductSourcesPanel.tsx:96-105`) and the setup step error (`SetupProgress.tsx:29-31`) need no code change — verify each by hand.

- [ ] **Step 1:** Implement the result display.
- [ ] **Step 2:** `npx tsc --noEmit && npm run lint` → clean.
- [ ] **Step 3:** In the running app with balance 0 (Django admin adjust): click "Re-run extraction", "Improve with AI", "Suggest mapping with AI", and start order setup → each shows the insufficient-credits message. With balance 5 and price 2, run extraction → the stopped Alert shows the right counts.
- [ ] **Step 4:** Commit `feat(frontend): show partial extraction results`.

---

### Task 14: Landing page pricing

**Files:**
- Create: `frontend/components/billing/PricingSection.tsx` (`"use client"`; fetch `billingApi.pricing()` on mount; render nothing on error; fixed-height skeleton while loading so the page doesn't jump)
- Modify: `frontend/app/page.tsx` — insert `<PricingSection />` between the Features section (ends line ~115) and the CTA (line ~117) with `id="pricing"`; add a `Pricing` link (`href="#pricing"`, `Button variant="ghost"`) in the header before `Sign in`; change CTA copy at line ~122 "Free during the beta. Set up your assistant in minutes." to "Start with free credits. Set up your assistant in minutes."

**Design plan (reviewed against the brief; executor refines with the skill):**
- Tokens: the site's existing neutral palette and Inter — no new fonts or colours; positive green only for the bonus figure (`text-emerald-600 dark:text-emerald-400`, already used ad hoc in the app).
- The one memorable element: **price per reply** as the large number on each card (`৳0.50`, `৳0.33`, `৳0.27` per reply — computed `price / replies`, 2 dp), with the pack price and credits beneath it. Under the three cards, a thin horizontal bar per pack whose length is its per-reply price relative to Starter, so "bigger is cheaper" reads at a glance without words.
- Highlighted (Growth) card is inverted (`bg-primary text-primary-foreground`) with a small "Most popular" badge; the others are plain bordered cards. No gradients, no shadows beyond the existing card.
- Heading "Pay for the replies you send"; sub "Buy credits once and use them on every channel. Bigger packs cost less per reply." Each card: name, per-reply price, "{price} for {credits + bonus} credits", bonus line "+{bonus} bonus credits" when > 0, "About {replies} AI replies*", feature list with check icons, full-width "Get started" button → `/sign-up`.
- Below the cards: "What uses credits" as a two-column definition list (`label` / "{credits} credit(s)"), plus "{signup_credits} free credits to start" when `signup_credits > 0`; footnote "*Estimate if credits are used only for AI replies; other actions use credits too." and "Payments are coming soon. For now we add credits to your project. Contact us to top up." (link "Contact us" to the existing contact/legal email from `lib/site.ts`).
- Hide the "About N replies" line when the reply price is 0.
- Responsive: one column under `md`, three columns from `md`; the per-reply comparison bars stack under the cards at all widths. Respect `prefers-reduced-motion` (no entrance animation needed at all).

- [ ] **Step 1:** Invoke `frontend-design:frontend-design`; confirm or revise the plan above against the existing landing page (screenshot first).
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** `npx tsc --noEmit && npm run lint` → clean.
- [ ] **Step 4:** Screenshot the landing page at 375px and 1280px, light and dark; check keyboard focus on the nav link and buttons; stop the backend and confirm the page still renders without the section.
- [ ] **Step 5:** Commit `feat(frontend): landing page pricing section`.

---

### Task 15: Whole-feature check

- [ ] **Step 1:** `docker compose exec django python manage.py test --noinput` → all pass.
- [ ] **Step 2:** `docker compose exec django python manage.py makemigrations --check --dry-run` → "No changes detected".
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit && npm run lint && npm run build` → success.
- [ ] **Step 4:** Walk spec section 9 acceptance criteria one by one against the tests above; list any without a test and add it to the owning task's test file.
- [ ] **Step 5:** Rollout note in the PR description: run migrations (allocation happens in `billing.0003`), check every project has a wallet in admin, confirm beat runs `billing-reconcile`.
