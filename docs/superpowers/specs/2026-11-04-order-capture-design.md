# Order Capture, Product Catalog & Order Actions — Design

Date: 2026-11-04
Status: Revised after user review (round 2)

## Why

Today a business can make its agent take orders only by writing free-text custom instructions. The order then lives only inside the chat: nobody can see it as data, filter it, or send it to a courier.

Most of our clients are **Facebook-first sellers** (Messenger, Instagram, sometimes WhatsApp). Some also have a website. Customers write in Bengali, Banglish and English.

We want ordering to be an **explicit feature**:

1. The client checks they meet the requirements, clicks **one button**, and everything the agent needs is set up for them. It is pre-filled from data we already have, and the client only reviews it.
2. Orders the agent takes appear as rows in an **Orders page** in our dashboard.
3. Staff can select orders and **push them to a courier or their own API** with actions they configure themselves.
4. The **product catalog stays up to date** with as little manual work as possible, matched by the client's own product key.

## Goals

- Requirements check, then "Start taking orders", then a pre-filled review, then Go live. The client should never need to write an instruction to take orders.
- **Every product the agent sells has a price.** Missing details are surfaced and the client is pushed to fill them in.
- The agent collects a complete, correct order in the customer's language, confirms it, and submits it exactly once.
- Totals come from the server, never from the LLM.
- The Orders page lets staff see, filter, edit and change the status of orders, and run custom actions on selected orders.
- Products come in automatically. The client picks which extracted products to import, and later updates match by the client's own primary key or SKU.
- The client can see the order prompt we apply, add their own order instructions, and get AI suggestions to improve them.

## Non-goals (this version)

- Payments (bKash, SSLCommerz, Stripe).
- Ready-made courier integrations. Couriers are reached through custom actions with fixed-key auth.
- Status messages pushed back to the customer.
- Facebook page post import (needs Meta App Review for `pages_read_engagement`).
- Shopify and WooCommerce sync.
- Photo-first bulk add.
- Stock **counting**. We track only available or not.
- Mobile push notifications (see Part 7).
- Fake-order detection, abandoned-order follow-up, order analytics.

## Anti-goals (failure even if it "works")

- The agent **sells or quotes a product without a catalog price**, or makes up a total.
- **Duplicate orders** from one confirmation.
- A sync or AI import **silently overwrites a value the owner set by hand**.
- Junk pages from a crawl **become products without the client choosing them**.
- The agent tells the customer "order placed" when no order exists.
- A custom action, live lookup or notification URL that can reach internal networks, or that leaks a client's secrets.
- Setup that still requires the client to write prompt text.

## Decisions made with the user

| Topic | Decision |
|---|---|
| Price | **Required to sell.** A product with no price (or another required detail missing) cannot be active. The client is prompted to fill it in. **No ordering unpriced products.** |
| Product matching | The client's own **primary key** (product id) is the match key. Fallback is SKU, then normalised name plus variant details. The UI warns clients to supply a key or SKU for reliable matching. |
| Custom product fields | Clients can add their own product fields. |
| Stock | **No counting.** Each product or variant is *available* or *unavailable*. Advanced option: a **live lookup** of price and availability from the client's API by product key. |
| Where orders go | Dashboard Orders page. |
| Notifications | One email per new order, **plus a POST-to-URL integration** with order and product details. More integration types come later. Mobile push only if cheap (it isn't; see Part 7). |
| Before setup | A **requirements check** (popup or screen) is shown before the feature can be enabled. |
| Crawl import | The client **chooses which extracted products to import** (crawls contain junk pages). Imported products keep their page URL, and their images are downloaded. |
| Currency | Per project, default BDT, **easy to find and change** on a new **Project settings** page. |
| Order prompt | The order settings page **shows the built-in order prompt** and lets the client add their own order instructions, which have an **"Improve with AI"** button. Order instructions are ordinary custom instructions marked as order-scoped. They also appear in the main custom instructions list. |
| Pushing orders out | Staff multi-select orders, then pick a custom action. **One HTTP request per order.** |
| Product sources | **The KB is the source**: product data comes from the client's knowledge base (documents, crawled site) plus their CSV/Excel/Sheet uploads. **Price learning from chat replies is out of scope.** |
| API integration auth | Actions, live lookup and the POST integration support **fixed-key auth only** (header with an API key or secret). This covers Steadfast and most clients' own APIs. Token-exchange auth (Pathao) and per-order area ids (RedX) come later with courier presets. |

**Note for "Create order from this chat":** we only see staff replies typed in our dashboard. Replies sent from the Meta inbox are dropped today (`channels_app/service/facebook_messing.py:99-100`), so the extraction sees only the customer's and the bot's side, plus dashboard replies. **Agreed: fix later** as a separate project (Meta echo capture).

## Scope

| In the first version | Later |
|---|---|
| **Project settings page** (currency and other project-level settings) | Facebook page post import |
| Product catalog separated from image search: price, client key, SKU, variants, custom fields, available flag | Shopify / WooCommerce sync |
| Product sources: KB/crawl extraction with **pick-what-to-import**, CSV/Excel, Google Sheet link | Photo-first bulk add |
| Image download and page URL for crawled products | Courier presets, token auth |
| Live price and availability lookup by product key (advanced) | Status messages to customers |
| | Mobile push |
| | Price learning from chat replies |
| Requirements check, then 3-step setup | Meta-inbox echo capture |
| Order settings page: visible built-in prompt, order instructions, Improve with AI | Fake-order detection, abandoned follow-up, analytics |
| Agent ordering tools, one draft per conversation, server-side totals | |
| Orders page, drawer, status history, CSV export | |
| Custom order actions (per order, background run, log, retry, optional auto-run) | |
| Email per new order and a POST-to-URL integration | |
| "Create order from this chat" | |

## Existing code to build on

- **Agent tools:** `agents/rag.py::_build_tools_from_context`.
- **HTTP safety:** `agents/api_tool.py` (public-host check, URL parsing, timeout, byte cap) and `knowledge/api_tool_config.py` (write-only secrets). Custom actions, live lookup and the POST integration all reuse these.
- **Webhooks:** `channels_app/webhooks.py` (`ProjectWebhook`, `emit_event`, signed, Celery-delivered); UI in `frontend/components/channels/WebhookSettings.tsx`.
- **Email:** `accounts/tasks.py::send_email`.
- **Instructions:** `knowledge.models.CustomInstruction` (`effective()`); UI in `frontend/components/knowledge/sections/InstructionsSection.tsx`.
- **Products:** `knowledge.models.Product` / `ProductImage`, `knowledge/products.py` (gated behind image search today), `knowledge.tasks.embed_product_image`.
- **Crawl:** `knowledge.models.Document` (`markdown_content`, `images_metadata`, `uri`, `parent_crawl_id`).
- **Settings UI today:** `frontend/app/(dashboard)/settings/page.tsx`. It mixes account cards (Profile, Password, Delete account) with a project card (Image search).
- **Celery beat:** `core/settings.py::CELERY_BEAT_SCHEDULE`.

## Part 0 — Project settings page

A new dashboard page, **Project settings**, holds project-level settings. The existing Settings page becomes account-only.

- **General:** project name, **currency** (searchable list, default BDT ৳; shown wherever prices appear).
- **Image search:** moved here from the account Settings page.
- **Webhooks / integrations:** the existing `WebhookSettings` moved or linked here, plus the order POST integration (Part 7).
- **Orders:** a link to Order settings (Part 3).
- *(Claude's call)* Further project-level settings found during the build go here too. Channel credentials stay on the Channels page.

## Part 1 — Product catalog

- **Separate the catalog from image search.** Every project can list, create, edit and import products. Image search stays gated as today and applies only to photo embedding and photo search.
- **Catalog stays per knowledge base** (`Product.kb`); the agent sees its conversation's KB products. *(Claude's call.)*
- **Product fields:**
  - `external_id`: the client's own primary key; optional, but unique per KB when set.
  - `sku`: optional.
  - `name`, `description`.
  - `price`: decimal, nullable.
  - `available`: bool, default true.
  - `status`: `needs_info` / `active` / `hidden`.
  - `product_url`: the source page.
  - `source`: `manual` / `kb` / `csv` / `sheet`.
  - `custom`: JSON, the values of the client's custom fields.
  - `locked_fields`: fields the owner edited by hand.
- **Price required to be active:** `active` requires a non-null price, **enforced as a DB check constraint**, not only in the UI. An import row missing a price, or missing a required custom field, lands as `needs_info`.
- **Needs-info prompting:** a persistent banner on the Products and Orders pages ("6 products need a price"), a count badge in the nav, and inline price entry (type, Enter, next).
- **Variants:** each has optional `external_id` / `sku`, its own price (falls back to the product price) and `available`. A product can be active only if every available variant ends up with a price.
- **Custom product fields:** defined per KB (label, type text/number/choice, required flag, "show to agent" flag). Values sit in `Product.custom`. Required custom fields count toward `needs_info`. Import mapping can target custom fields.
- **Key warning:** wherever products are imported or created, if no `external_id` / SKU is supplied, show: *"Add your product ID or SKU so updates match the right product. Without it we match by name, which can mix up similar products."*

## Part 2 — Product sources and updates

**Matching rule for every source, in order:**
1. `external_id`
2. `sku`
3. Normalised name plus variant attributes (case, spaces, punctuation, Bengali/English digits)

Rule 3 is shown with a "matched by name" marker in the review UI so the client can correct wrong matches.

**Update rules:**
- **Price and availability updates:** a source row that matches an existing product updates its price and `available`. Clearing a price in the source sends the product back to `needs_info` (off sale) and notifies the owner.
- **Owner always wins:** fields in `locked_fields` are never changed by a sync.
- **Removed from the source:** the product is hidden, never deleted.
- **Trust:** CSV and Sheet changes apply automatically. Changes the AI read (KB extraction) go to review.
- **Price parsing** handles `৳1,200`, `1200/-`, `Tk 1200`, `BDT 1200`, Bengali digits, and sale vs regular price. A price it can't parse leaves the price empty, so the product goes to `needs_info`.

**Sources:**

1. **KB / crawl extraction with pick-what-to-import.**
   - An LLM extraction over the KB's documents and crawled pages produces **candidates**: name, price, variants, page URL, image URLs, guessed custom-field values, and a confidence.
   - **Nothing becomes a product until the client picks it.** The import screen lists candidates grouped by source page, with the page URL, thumbnail, extracted price, and a low-confidence highlight.
   - Junk pages are visually obvious, and there is "Select all from this page" plus "Ignore this page next time".
   - On import:
     - The product page URL is saved.
     - **Images are downloaded into `ProductImage` storage** (R2), whether or not image search is on, so turning image search on later just runs embedding. Embedding runs only when image search is on.
   - Re-running after a re-crawl shows only **new candidates and changes** to imported products ("Red saree 450 → 500").
2. **CSV / Excel upload.** Column mapping is guessed and the client confirms it, including the key column and custom fields. The mapping is saved. The key warning is shown when no key column is mapped.
3. **Google Sheet link.** The sheet must be shared as "anyone with the link can view", with a clear error when it isn't. It is pulled as CSV on a Celery beat schedule and on "Sync now", using the same mapping.
4. **Live lookup (advanced).** Configured in Order settings (Part 3).
   - Setup: GET URL template with `{{product.external_id}}` (or SKU), headers (write-only secrets), and JSON paths for price and availability.
   - When it's called: by the agent's find-products tool for the products it returns, and again at submit.
   - Results are cached briefly (*Claude's call:* a few minutes).
   - **If the lookup fails or times out,** the catalog values are used and the failure is logged.
   - A lookup that returns no price never makes an unpriced product sellable.
   - It goes through the same HTTP safety as actions.

## Part 3 — Requirements check, setup and order settings

**Requirements check (shown before enabling):** a popup when the client clicks "Start taking orders". Hard requirements block enabling; each one has a "Set up" link:
- ✅ / ❌ At least one **connected messaging channel** (FB, IG, WA or widget). **Hard.**
- ✅ / ❌ A **product source**: a KB with documents or a crawl, or an uploaded CSV or Sheet. **Hard.**
- ✅ / ⚠️ **Currency** set (default BDT is fine; shown so the client notices).
- ✅ / ⚠️ **Delivery and payment info** in the knowledge base (helps pre-fill; can be entered in step 2).
- ⚠️ **Product IDs / SKUs** recommended for reliable updates.

**Then the three-step setup** (mockup: `.superpowers/brainstorm/998-1793786941/content/setup-wizard.html`):
1. **"We set it up for you":** a background job extracts product candidates, delivery zones and charges, and payment methods, and reports what it found and where, plus what wasn't found.
2. **Settings:**
   - Channels that take orders.
   - Delivery zones and charges (default Inside / Outside Dhaka).
   - Payment methods (labels; COD default).
   - Order form fields: name, phone, address, zone required; note optional; custom fields allowed.
   - Notification emails.
3. **Pick products:** the pick-what-to-import screen from Part 2.
   - Picked candidates with a price → `active`.
   - Picked candidates without one → `needs_info`, with inline price entry right there.
   - **"Go live" needs at least one active product.** If any picked product still needs info, show "N products still need a price; they won't be sold until fixed".
   - "Go live" accepts the prices shown for picked rows. Low-confidence ones are highlighted first.

**"Try it"** opens a test chat (orders marked `is_test`). **Turning it off** removes the order tools and keeps existing orders.

**Existing order instructions:** setup scans `CustomInstruction` rows for order-taking text and `call_api` order pushes, and offers one-click to turn them off or **convert them to order instructions**. It never deletes them silently.

**Order settings page** (Project settings → Orders; editable after setup):
- Everything from step 2, plus live lookup (advanced), custom actions (Part 6) and notifications (Part 7).
- **"What we tell your agent":** a read-only view of the **built-in order prompt**, rendered with the client's current settings (zones, payment, form), so they see exactly what is applied.
- **Your order instructions:**
  - These are ordinary `CustomInstruction` rows with a new `scope` = `order`.
  - They are injected **inside the order prompt block, after the built-in rules**, and only when ordering is on.
  - They are also listed in the existing custom instructions list with an **"Orders" badge**, editable from either place.
  - Built-in rules that protect correctness are stated as non-overridable in the prompt: catalog prices only, server totals, confirm before submit, order number required.
- **"Improve with AI"** button on the instruction editor:
  - Sends the instruction text (plus the built-in prompt as context for order instructions) to the LLM.
  - Shows a suggested rewrite with a short "why", and the client accepts, edits or discards it. Nothing is applied automatically.
  - *(Claude's call)* The editor is shared, so the button also works on general instructions.

**Prompt block** *(Claude's call)*: the order behaviour is a built-in prompt block, not editable `CustomInstruction` text, so we can improve it for everyone. Client customisation is the order-scoped instructions.

**Guardrails for client-edited order instructions.** Clients can write anything in their order instructions, so the system must stay correct whatever they write. There are three layers:

1. **Enforced in code (the prompt can't change these):**
   - Prices come only from the catalog or live lookup. The tools have **no price or discount argument**.
   - `needs_info` and unavailable products are never sellable.
   - Totals are calculated on the server.
   - Submit checks required fields, every line's price and availability, and that product and variant ids belong to the conversation's KB.
   - Quantities are positive whole numbers with a per-line cap (*Claude's call:* 100).
   - Submit is idempotent. The order number comes only from the tool.
   - The agent can't set staff-only fields: statuses beyond `new` / `cancelled`, `advance_paid`, internal note, tracking number.

   These same checks also protect against customers trying prompt injection.
2. **Prompt structure:**
   - Client order instructions are placed inside a clearly delimited "business preferences" section.
   - The protected rules are stated **both before and after** that section, and the prompt says the preferences can't override them.
   - Order instructions are capped in total length (*Claude's call:* 2,000 characters).
3. **Check on save:**
   - When an order instruction is saved (or an "Improve with AI" suggestion is accepted), an LLM check looks for conflicts with the protected rules and for unsafe requests. Examples: give discounts; skip confirmation; take orders for unpriced items; promise prices; collect card numbers, passwords or OTPs.
   - Conflicts are shown as a warning explaining that "this won't take effect because…". Unsafe-data requests are **blocked**.
   - Other conflicts are warned about, not blocked, since the code enforces the protected rules anyway.
   - "Improve with AI" never suggests text that conflicts with them.

## Part 4 — Agent ordering behaviour

Registered only when ordering is enabled **and** the conversation's channel is enabled.

Tools (names and signatures left to the builder):
- **Find products:** search active, available products in the conversation's KB. Returns name, variants, price, availability and "show to agent" custom fields, refreshed by live lookup when it's configured. **Products with `needs_info` are never returned as sellable.**
- **Update draft:** set items and customer fields on the conversation's **single open draft**. It returns missing fields, unknown or unavailable items, and the server-computed summary and total.
- **Submit order:** allowed only when nothing required is missing and **every line has a price and is available** (re-checked, with live lookup if configured). It assigns the order number and is idempotent.
- **Change or cancel:** while the order is `new`. After staff confirm it, the agent hands off.

Built-in prompt rules:
- Read back a summary and get an explicit yes.
- Confirm only with the order number returned by the tool.
- Never change a price or give a discount (hand off).
- Product not found, needs info, or unavailable: say so and suggest alternatives; for a product that exists but has no price, **hand off** ("our team will get back to you"). **Never take an order for it.**
- Repeat customer: offer the last address and phone.
- Use the customer's language.

Guards:
- **Totals:** sum of lines + zone charge.
- **BD phone normalisation:** `+880`, `880`, `01XXXXXXXXX`, Bengali digits.
- **Validation** runs against the current settings.
- **Availability** is re-checked at submit.
- **Drafts expire** to `abandoned` after N hours (default 48).
- **Bot paused:** staff use "Create order from this chat".
- **Hallucinated confirmations** are logged in the reply trace.
- **A handoff because of a missing price** links staff to that product's price editor, so one fix unblocks future customers.

## Part 5 — Orders page

Mockup: `.superpowers/brainstorm/998-1793786941/content/orders-actions.html` (the "Price pending" row there is obsolete; unpriced orders no longer exist).

- **Order fields:** project, conversation, contact, channel, KB, `number` (per project, from 1001, concurrency-safe), status, items **snapshot** (key, SKU, name, variant, unit price, quantity, ids), customer fields, zone and charge, totals, `advance_paid` (staff-editable, default 0), `collect_amount` (= total − advance), `is_test`, tracking number, internal note, timestamps.
- **Statuses:** `draft` (hidden), `new`, `confirmed`, `shipped`, `delivered`, `cancelled`, `abandoned` (hidden by default).
- **Status history:** who (user or agent), from, to, when.
- **List:** search, filters (status, channel, date, test), CSV export.
- **Action bar** on selection: *Set status*, *Run action ▾*.
- **Drawer:** items (editable; totals recompute), customer, linked chat, history, action log.
- **The conversation view** shows that conversation's orders, plus **"Create order from this chat"** (an LLM pre-fill that staff edit). Staff can only pick catalog products, and **products that need info must get a price first** (inline editor), so staff orders follow the same "priced only" rule.
- **Permissions** *(Claude's call)*: members view, edit and run actions; admins change settings, actions and integrations.

## Part 6 — Custom order actions

- **Action:** name, GET / POST, URL, headers (write-only), body template, response mapping (JSON path → `tracking_number`), success status, **"Run automatically on new orders"** (default off).
- **Templates:**
  - Placeholders: `order.*` (including `collect_amount`, `advance_paid`), `customer.<field>`, `zone.name`, `order.items` (with `external_id` / SKU per line).
  - Values are JSON-encoded in JSON bodies and URL-encoded in URLs, never plain string replacement.
  - A template that doesn't produce valid JSON is rejected on save.
- **Run:**
  - One request per order, in a Celery job, with progress.
  - The same HTTP safety as `agents/api_tool.py`, plus no redirects to non-public hosts.
  - A run record per order (secrets redacted).
  - 2xx → apply the mapping and status; failure → recorded; the other orders continue.
- **Guards:** "already sent by this action" asks before re-sending; a missing placeholder value fails only that order; retry; "Test with order #N".
- **Auth:** fixed-key headers only in this version (see Decisions).
- **Secrets:** write-only like API-tool secrets. Encryption at rest is deferred, as for existing secrets.

## Part 7 — Notifications

- **Email per new order** (test orders excluded), using the branded `send_email`.
- **POST-to-URL integration:** the client enters a URL (and an optional secret header). We POST a fixed JSON payload with the order, customer, and **line items with product details** (key, SKU, name, variant, price, product URL) for every new order. It is signed and retried.
  - *(Claude's call)* This is built on the existing `ProjectWebhook` / `emit_event` (event `order.created`, plus `order.status_changed`) rather than a new mechanism. It is shown under Order settings → Notifications as "Send new orders to a URL".
  - Its payload is fixed. A client who needs a custom body shape uses a custom action with "Run automatically on new orders".
  - The settings list is shaped to allow more integration types later (Slack, Telegram and so on).
- **Mobile push: skip in this version.** Browser/mobile web push needs:
  - a service worker and PWA install (required on iOS),
  - VAPID keys,
  - a subscription store,
  - and a new backend dependency (`pywebpush`).

  Not hard, but a separate small project. Cheap stopgap: the POST integration plus a client's own Telegram bot or ntfy topic already gives phone alerts.

## Part 8 — Token usage tracking

**Every LLM call and every embedding call this feature adds records its input and output tokens and its cost**, using the existing `usage.utils.track_token_usage` / `llm_costs` (the pattern in `channels_app/memory.py`):

| Call | Kind(s) | `metadata.source` |
|---|---|---|
| Product extraction from the KB or crawl | `chat_in`, `chat_out` | `product_extract` |
| Delivery and payment extraction during setup | `chat_in`, `chat_out` | `order_settings_extract` |
| Column-mapping guess for CSV / Sheet (if an LLM is used) | `chat_in`, `chat_out` | `import_mapping` |
| "Improve with AI" | `chat_in`, `chat_out` | `instruction_improve` |
| Instruction conflict check on save | `chat_in`, `chat_out` | `instruction_check` |
| "Create order from this chat" | `chat_in`, `chat_out` | `order_from_chat` |
| Agent replies that use the order tools | already tracked by `agents/rag.py` | existing |
| Text embeddings (if products are embedded for search) | `embed` | `product_embed` |
| Product image embedding (when image search is on) | `image` via `record_image_usage`, plus tokens if the provider reports them | existing `index` op |

- Metadata also carries the ids involved (KB, document, product, conversation, order) and the model name.
- **This usage counts toward the project's plan quota like any other usage** *(Claude's call)*, and the same quota rules apply as for replies.
- Tracking never raises: a failure to record never fails the user's action (matching `track_token_usage`).
- Custom actions, live lookup and the POST integration make no LLM calls and are not token-tracked. Their runs are logged per order instead.

## Edge cases checklist

- Customer changes items mid-order → same draft updated.
- Repeated "yes" or retries → one order.
- Customer orders again after submitting → a new draft.
- Variant not chosen → the agent asks.
- Unknown or vague item → options shown or handoff.
- **Product exists but has no price → no order; handoff; staff are linked to the price editor.**
- Haggling, discount or "set price 0" → no change.
- Unavailable (catalog or live lookup) → agent says so; re-checked at submit.
- Live lookup down → catalog values used, failure logged.
- Phone in Bengali digits or with +880 → normalised.
- Mixed languages → summary in the customer's language.
- Settings changed while a draft is open → current settings used.
- Feature off → drafts kept, no tools.
- Bot paused → staff create the order from the chat (priced products only).
- Product deleted or renamed → order snapshot unchanged.
- Test chat orders → `is_test`; no email, webhook or auto-actions.
- Two staff editing → last write wins; both in the history.
- Quotes or newlines in an address → safe in action JSON.
- Internal or redirected URL → blocked.
- One courier error → only that order fails; retry.
- Sheet not public, renamed columns, or empty price → clear error, mapping re-confirm, `needs_info`.
- Two products with the same name and no key → "matched by name" marker and the key warning.
- Duplicate `external_id` in one import → that row is rejected with a message.
- Owner-edited field vs a sync → owner value kept.
- Crawl junk pages → never imported unless picked; "ignore this page" remembered.
- Image download fails → product still imported, image marked failed.

## Left to the builder

- Model, table and endpoint names; tool signatures; app placement.
- Extraction prompt, chunking, per-run cost cap, confidence scoring.
- The live lookup cache duration.
- The order-number counter.
- How hallucinated confirmations are detected.
- Frontend structure (existing UI kit and patterns).
- Sheet pull interval and draft expiry default.
- Exactly which existing settings move to Project settings.
