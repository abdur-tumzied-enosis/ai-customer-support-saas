# Orders setup simplification: plan

Status: not started. Tick a box when the task is done and checked. Update the "Progress" table as phases close.

## FE design ;
for FE design use frontend design skills; 

## Problem

1. Setup is a one-time job but lives in the permanent Orders sidebar, with its own copy of the settings screen.
2. Picking products happens in the setup wizard, but fixing their prices happens in Knowledge > Products. The user doesn't know why they have to leave.

## Target design

```
Sidebar (never changes):   Orders · Order settings

Orders page, orders off:   the panel is the whole page (no empty table): "Start taking orders" [Set up orders]
                           or [Continue setup] if settings were already saved
Setup flow (/orders/setup): no Orders sidebar. Header: "Set up orders", "Settings" then "Products", [Close]
  Step 1 Settings   waits for the setup job, shows what was found, form is the same one as Order settings
  Step 2 Products   zone A: add from your website (candidates)
                    zone B: your products, rows that need a price first, price editable in the row
                    [Turn on orders] with the reason next to it when disabled
```

## Decisions made (change here if you disagree)

| # | Decision | Why |
|---|----------|-----|
| D1 | The setup job (today's "We set it up" step) stops being a step. It starts when the user enters the flow. Step 1 shows its result as one line ("Found 3 delivery zones, no payment methods"). | The job pre-fills zones, payments and product candidates, so it must still run. |
| D2 | Add `settings_confirmed_at` to `OrderSettings`, set when the user saves settings. Current step = confirmed ? Products : Settings. | The job fills zones itself, so "zones exist" does not mean the user reviewed them. `setup_status` only describes the job. |
| D3 | URL `?step=` may move back, never forward. | No skipping ahead. |
| D4 | Step 1 shows live job progress and the form stays hidden until the job is `ready`. A failed or interrupted job shows [Try again from here], [Start over] and, when the failure is not fixable by the user, [Fill in myself]. | The job pre-fills the form. Showing progress, not a blank spinner, explains the wait and survives leaving the page. |
| D5 | Step 2 uses `CandidateReview` (zone A) plus `ProductTable` (zone B). No new price cell. | Candidates are not products until imported; `ProductTable` already has Enter-to-save and focus-next. |
| D6 | `RequirementsDialog` is replaced by the checklist inside the Orders page panel. Keep the two hard checks (channel, product source) and the "Try it" button. Delivery and product-code checks are handled inside the flow. | The dialog's help text for delivery and product keys duplicates the flow. |
| D7 | Keep the URL `/orders/setup`. Keep the main dashboard sidebar. Only the inner Orders sidebar goes. | No redirects, and no move out of the `(dashboard)` group. |
| D8 | Turn on orders stays allowed when at least one product is active. | Already the current rule (`active === 0` disables it). |
| D9 | Progress is stored on the server as a list of steps (`setup_steps`), each with `status` (pending, running, done, failed, needs_input), a short user-facing detail, and a heartbeat time. The UI only renders what the server says, so leaving and coming back shows the same state. | State must survive navigation and reloads. |
| D10 | Three visible steps: "Check your website and documents", "Find your products", "Find delivery and payment details". The last one is one model call today, so it stays one row. | Matches the real units of work. |
| D11 | Retry resumes from the first step that is not done. Start over resets every step. Neither touches products the user imported or settings the user saved: zones and payment methods are only filled when empty, and start over only deletes candidates still pending. | A retry must never destroy user work. |
| D12 | A step that needs the user (for example no website or documents yet) ends as `needs_input` with a plain message and a link (Knowledge > Websites). A step with no heartbeat for 5 minutes is shown as failed ("Setup was interrupted"). Raw exceptions are logged, not shown. | Covers "the user must add something" and "the system went down". |

## Design review (UI)

Reviewed against the frontend-design guidance. This is an admin tool inside an existing app, so the aim is consistency with the current shadcn theme, not a new look. No new palette, no new typeface, no entrance animations. The one memorable element is the setup progress checklist; everything else stays quiet.

### Copy and vocabulary (one name per action; replaces any older wording in this file)

| Where | Text |
|-------|------|
| Orders page, orders off | Title "Start taking orders", button "Set up orders" (or "Continue setup") |
| Setup flow | Title "Set up orders", two steps "Settings" and "Products", "Close" |
| Final button | "Turn on orders" (pairs with the existing "Turn off orders") |
| After it succeeds | Orders page shows "Orders are on. New orders from your chats appear here." The sidebar pill reads "Taking orders" |
| Retired words | "Go live", "You're live", "Before you go live", "We set it up", "Pick products" |

### Findings and what to do

- [ ] Two kinds of "step" on one screen (flow steps and job tasks). Number only the flow's two steps. Job rows are tasks and carry no numbers. No "step 2 of 3" anywhere; progress text says what is happening ("Reading page 6 of 14").
- [ ] Failure UI: one primary button per failure ("Try again"), "Start over" as a quiet link. The status is never colour alone: each row has an icon plus a visually hidden "Done / Running / Failed / Waiting". Failed row uses `role="alert"`; the running list uses `aria-live="polite"`.
- [ ] Empty states are invitations: orders off and no orders = the panel only. Products step with nothing imported shows only "Add products from your website"; zone B appears once at least one product exists (no "No products here" table).
- [ ] Products step order: add (zone A) first, your products (zone B) below, rows that need a price pinned at the top with a "Needs a price" badge (text plus colour). Keep warning text readable in dark mode (`text-amber-700 dark:text-amber-400`).
- [ ] Mobile (375px): the flow's product table drops the SKU and Edit columns (name, price, status only). The sticky footer must not cover the last row: add bottom padding equal to its height. The disabled-button reason sits above the button on small screens.
- [ ] "Close" in the setup header: if the settings form has unsaved edits, ask before leaving ("Close without saving?"). Progress of the job is kept either way.
- [ ] Panel is a single card with one button. No stat tiles, no gradient, no icons-in-circles. Explanation is one sentence of 80 characters or fewer per line.
- [ ] Motion: none except the spinner and the row state change (and respect `prefers-reduced-motion` for the spinner).
- [ ] Screenshot review at 375px and 1280px, light and dark, for: panel, step 1 running, step 1 failed, step 1 needs-input, step 2 with unpriced rows, step 2 ready. Fix before calling Phase 4 done.

## Progress

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Backend: settings confirmed flag | [ ] |
| 2 | Routes and sidebar | [ ] |
| 3 | Orders page entry panel | [ ] |
| 4 | Setup flow | [ ] |
| 5 | Cleanup and verification | [ ] |

## Phase 1: Backend (settings confirmed flag)

- [ ] Add `settings_confirmed_at = DateTimeField(null=True, blank=True)` to `OrderSettings` (`backend/orders/models.py`) and a migration.
- [ ] Set it in the settings PATCH handler (`SettingsView` in `backend/orders/api.py`) when a user saves zones, payment methods, channels or emails. Do not set it from the setup job.
- [ ] Return it from settings GET. Add `settings_confirmed_at: string | null` to `OrderSettings` in `frontend/lib/orders-api.ts`.
- [ ] Tests in `backend/orders/tests/`: PATCH sets it; the setup job does not; GET returns it.
- [ ] Check that disabling orders does not clear it (turning orders back on must not restart setup).
- [ ] Add `setup_steps = JSONField(default=list)` to `OrderSettings` (same migration). Each item: `key`, `label`, `status`, `detail`, `error_code`, `error`, `started_at`, `finished_at`, `heartbeat_at`.
- [ ] Rewrite `backend/orders/setup.py`: replace the Celery `chain` with one `run_step(project_id, kb_id, key)` task per step. Each task marks its step running, does the work, marks it done (with `detail`, for example "Found 12 products"), then queues the next step. Any exception marks the step failed and stops the run, with a user-safe message and `error_code`; the exception goes to the log.
- [ ] Step 1 (check content): `needs_input` with code `no_content` when the KB has no documents with text.
- [ ] Step 2 (products): before extracting, delete the KB's candidates that are still `pending` (the extractor has no duplicate check, so a retry would double them). Update `heartbeat_at` after each page. A run where every page fails already raises; keep that as `failed`.
- [ ] Step 3 (delivery and payment): keep the one call. Save only `zones`, `payment_methods`, `setup_report` with `update_fields`, and only fields that are still empty.
- [ ] `run_setup(project_id, kb_id, mode)`: `mode` is `resume` (default; start at the first step not done) or `restart` (reset all steps). Guard against two runners: refuse with 409 when a step is running with a fresh heartbeat (compare-and-set update).
- [ ] Status endpoint (`SetupStatusView`) returns `status` (`not_started`, `running`, `ready`, `failed`, `needs_input`), `steps`, `report`. A running step whose heartbeat is older than 5 minutes is returned as failed with code `interrupted`.
- [ ] `SetupRunView` accepts `mode`. Keep `setup_status` in sync or derive it from `setup_steps`; no other code should read two sources.
- [ ] Tests: steps advance in order; a failure stops the run and records the code; resume skips done steps; restart resets them; retry does not duplicate candidates; a stale heartbeat reads as interrupted; a second run is refused while one is fresh; imported products and saved zones are untouched by restart.

## Phase 2: Routes and sidebar

- [ ] Move the orders list into a route group so the setup route can have a different layout:
  `orders/(shell)/layout.tsx` (renders `OrdersShell`) and `orders/(shell)/page.tsx`; delete `orders/layout.tsx`. URLs do not change.
- [ ] Add `orders/setup/layout.tsx`: minimal header (title, step indicator, Exit to `/orders`), no `OrdersShell`.
- [ ] `OrdersShell.tsx`: delete `STEPS`, `showSteps`, `inSetup`, the Setup group and the `useSearchParams` step read. Rename "All orders" to "Orders". Remove the now unneeded `Suspense` wrapper if nothing else in the shell reads search params.
- [ ] Keep the "Taking orders / Orders are off" status pill.
- [ ] Check `project-settings/orders/layout.tsx` still wraps the shell and the sidebar looks the same before and after enabling orders.

## Phase 3: Orders page entry

- [ ] Replace the "Start taking orders" card (`orders/page.tsx:122-131`) with a panel: title "Start taking orders", one line of explanation, and a [Set up orders] button. When `settings_confirmed_at` is set but orders are off, the button says "Continue setup". While orders are off and there are no orders, show only the panel: hide the filters, table and Export CSV.
- [ ] Show the two hard blockers (channel, product source) in the panel, from `orderApi.requirements`, with links to Channels and Knowledge. Hide the help text for delivery and product keys.
- [ ] Keep "Try it" (test mode) in the panel.
- [ ] Remove `RequirementsDialog` once the panel covers it (or reduce it to the blocker list). Remove its `onContinue`/`onTryIt` wiring from `orders/page.tsx`.
- [ ] Make sure enabled projects never see the panel.
- [ ] When the setup job is running, failed or waiting for input, show that on the panel ("We're reading your website and documents", "Setup stopped. Open it to see why") with the button going to `/orders/setup`. This is how a user who left the flow sees progress when they come back.

## Phase 4: Setup flow

- [ ] `SetupWizard`: derive the step from `settings_confirmed_at` (D2, D3). Drop the `router.push(?step)` navigation except for Back.
- [ ] On entry: `not_started` starts the job; `running` only polls (never starts a second run); `failed` or `needs_input` shows the problem and waits for the user; `ready` goes straight to the form. Never auto-retry.
- [ ] New `SetupProgress` component: a checklist with one row per step (done check, spinner, failed cross, empty circle), the step's detail text, and an `aria-live="polite"` region so screen readers hear changes. Poll the status endpoint every 2 seconds while running. It renders only server state.
- [ ] Failed row: the plain message, then a primary [Try again] (resume; the failed row already names the step) and a quiet [Start over] link (restart). Never repeat the step name inside the button. `needs_input` row: the message plus its link (for example "Add a website or document"), and a [Check again] button that resumes.
- [ ] [Fill in myself] shows the settings form without the job's results. Only offered on `failed` and `needs_input`; safe because the job saves with `update_fields` and only into empty fields.
- [ ] Step 1 (Settings): while the job runs show `SetupProgress`; after `ready`, show a one-line summary from the report plus the settings form (`SetupStepSettings`). Keep a small [Run again] link that uses restart. Button: "Save and continue".
- [ ] Step 2 (Products), zone A: `CandidateReview` as it is today.
- [ ] Step 2 (Products), zone B: `ProductTable` with needs-a-price rows first. Pass `onSavePrice` through `productApi.patch` (same as `ProductsSection.tsx:183`). Add save on blur.
- [ ] Copy under the table: "N products ready to sell. M need a price." The "M need a price" text jumps to the first empty price field.
- [ ] Turn on orders: when disabled, write the reason beside the button and connect it with `aria-describedby`. On success go to `/orders` and show the "Orders are on" empty state there (see Design review).
- [ ] Confirmation text when some products have no price: "2 products without a price stay hidden until you add one in Knowledge > Products."
- [ ] Mobile layout at 375px: step indicator on one line, the product table scrolls inside its container, no horizontal page scroll.
- [ ] Keyboard: visible focus everywhere, Enter in a price field saves and moves to the next empty price.

## Phase 5: Cleanup and verification

- [ ] Delete `SetupStepSetup.tsx` if its logic moved into the wizard. Remove unused `LABELS`, imports and the `?step=` query use.
- [ ] Order settings page (`project-settings/orders/page.tsx`): drop the duplicate "Settings" heading inside `SetupStepSettings` when rendered under the section title.
- [ ] `npx tsc --noEmit` and lint pass in `frontend/`.
- [ ] Backend tests for `orders` pass.
- [ ] Browser check with Playwright: (a) new project, KB with crawled site: full setup to live; (b) setup abandoned after step 1, then "Continue setup"; (c) enabled project: sidebar has two items, no panel; (d) turn orders off, then on.
- [ ] Update `docs/superpowers/specs/2026-11-06-unified-products-design.md` if the product-step behaviour changes what that spec says.

## Risks and open questions

- The setup job's `settings.save()` rewrites the whole row (`backend/orders/setup.py:91`). Phase 1 narrows it to `update_fields` on empty fields so "Fill in myself" and retries are safe.
- `extract_candidates` swallows single-page failures and only raises when every page fails (`knowledge/product_extract.py:148`). A run with some failed pages reads as done; the detail line should say how many pages could not be read.
- Real failure causes are unverified. Check the worker logs for typical errors (model timeouts, empty pages) and map them to `error_code` values before writing the user-facing messages.
- Pre-import price edits on candidates are not supported by the import endpoint, so a price can only be added after import (D5).
- The wizard still uses the default KB only (`SetupWizard.tsx:27`). Not changed by this plan.
- The uncommitted work on this branch touches most of these files. Commit it first so the plan's diffs are readable.
