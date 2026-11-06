# Order Capture C: Custom Actions, Notifications and Live Lookup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Part of the order-capture series; see the index at `docs/superpowers/plans/2026-11-05-order-capture-index.md`. **Requires Plans A and B merged.**

**Goal:** Staff select orders on the Orders page and push them to a courier or their own API, one HTTP request per order, with a run log and retry. The client gets an email per new order and a signed POST to their own URL with full product details. Advanced clients can have live price and availability looked up from their API by product key.

**Architecture:** Actions, live lookup and the POST integration all go through one outbound helper, `orders/http.py`, which reuses the host checks in `agents/api_tool.py`. Templates are rendered by a small placeholder renderer that encodes values for their context. The POST integration reuses `channels_app/webhooks.py` (`emit_event`) rather than a new mechanism. Plan B's hooks (`order_submitted`, `order_status_changed`) and pricing seam (`quotes_for`) get their bodies filled in here.

**Tech Stack:** Django 5 / DRF, Celery, `requests`, Django cache (live-lookup TTL), Next.js (TypeScript) with the existing UI kit.

**Spec:** `docs/superpowers/specs/2026-11-04-order-capture-design.md` (Parts 2.4, 6, 7, and the C rows of Part 8). Read it before starting.

## Global Constraints

- Fixed-key auth only: a header with an API key or secret. No token exchange.
- Action headers and secrets are write-only. Responses return the header name and `has_value`, never the value.
- Action body templates: values are JSON-encoded in JSON bodies and URL-encoded in URLs. Never plain string replacement. A template that is not valid JSON is rejected on save.
- Placeholders: `order.*` (including `collect_amount` and `advance_paid`), `customer.<field>`, `zone.name`, `order.items` (with `external_id` and SKU per line).
- HTTP safety for all outbound calls: public hosts only (same check as `agents/api_tool.py`). No redirects. Timeout 10 s. Response body capped at 20,000 bytes (same as `MAX_BYTES` in `agents/api_tool.py`).
- One request per order for actions. One failing order never stops the others.
- A run record per order request. Secrets are redacted from run records and errors.
- Test orders (`is_test=True`) trigger no email, no webhook and no automatic action.
- Live lookup cache: 5 minutes (Claude's call). Submit always bypasses the cache.
- If a live lookup fails or times out, catalog values are used and the failure is logged. A lookup that returns no price never makes a product sellable.
- Sheet and live-lookup calls and action calls go through the same `safe_request`.
- The POST integration payload is fixed. A custom body shape uses a custom action with "Run automatically on new orders".
- Email: one per new order, sent to `OrderSettings.notification_emails` through `accounts/tasks.py::queue_email`.
- Permissions: members run actions and view run logs. Admins create, edit and delete actions, and edit live lookup and notifications.
- Response envelope: `{"success": true, "data": ...}` or `{"success": false, "error": {"code", "message"}}`.
- Tests: `docker compose run --rm django python manage.py test <dotted.module> -v 2`. Frontend: `cd frontend && npx tsc --noEmit && npm run lint`.

## Review Focus

1. **An internal or redirected URL is reached** by an action, live lookup or the POST integration. Tests: `test_action_internal_url_blocked` (Task C2), `test_redirect_not_followed` (C2), `test_live_lookup_internal_url_blocked` (C8).
2. **A secret leaks** through an API response, a run record or an error string. Tests: `test_headers_write_only` (C4), `test_run_error_redacts_secret` (C5).
3. **One courier error stops the other orders** in a bulk run. Test: `test_one_failure_does_not_stop_others` (C5).
4. **A test order triggers email, webhook or an automatic action.** Test: `test_test_order_triggers_nothing` (C6).
5. **An order is sent to a courier twice** without an explicit confirmation. Test: `test_already_sent_needs_force` (C5).

---

### Task C1: Action and run models, live-lookup setting

**Files:**
- Modify: `backend/orders/models.py` (add `OrderAction`, `OrderActionRun`; add `live_lookup` JSON to `OrderSettings`)
- Create: migration under `backend/orders/migrations/`
- Create: `backend/orders/tests/test_action_models.py`

**Interfaces:**
- `OrderAction`: `project` FK (related `order_actions`, CASCADE). `name` (255). `method` (`GET` / `POST`). `url` (Text). `headers` (JSON list of `{"name", "value"}`). `body_template` (Text, blank). `response_mapping` (JSON dict; only key `tracking_number` in this version). `success_status` (CharField 16, blank; one of the order statuses or blank). `success_codes` (JSON list of ints, default `[200, 201, 202, 204]`). `auto_run` (Bool False). `is_active` (Bool True). `created_at`, `updated_at`. Constraint: `UniqueConstraint(fields=["project", "name"])`.
- `OrderActionRun`: `action` FK (related `runs`, CASCADE). `order` FK `Order` (related `action_runs`, CASCADE). `status` (`queued` / `running` / `succeeded` / `failed`). `attempt` (PosInt, default 1). `http_status` (null). `error` (Text, blank). `response_excerpt` (Text, blank, max 500 characters, redacted). `actor_type`, `actor_id`. `created_at`, `finished_at` (null).
- `OrderSettings.live_lookup`: JSON dict default `{}`. Shape: `{"enabled": bool, "url_template": str, "headers": [{"name", "value"}], "price_path": str, "available_path": str | null}`.

- [ ] **Step 1: Write the failing tests**: `test_action_name_unique_per_project`, `test_run_per_order_allowed_many`, `test_live_lookup_defaults_empty`.

- [ ] **Step 2: Run to verify failure.** `docker compose run --rm django python manage.py test orders.tests.test_action_models -v 2`.

- [ ] **Step 3: Implement** the models and run `makemigrations orders`.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): action, run and live-lookup models`

---

### Task C2: Outbound HTTP helper

**Files:**
- Create: `backend/orders/http.py`
- Create: `backend/orders/tests/test_http.py`

**Interfaces:**
- `HttpResult` dataclass: `status: int | None`, `text: str`, `ok: bool`. `text` is decoded from at most `MAX_BYTES` bytes and ends with `"\n[truncated]"` when cut.
- `OutboundError(Exception)`: raised for an unsafe URL, a timeout or a connection failure. The message contains no URL query and no header value.
- `safe_request(method: str, url: str, headers: dict[str, str], body: str | None = None, timeout: float = 10) -> HttpResult`:
  - Parses the URL with `agents.api_tool._parse_target`. Requires `http` or `https`, and a non-empty host.
  - Checks `agents.api_tool._is_public_host(host)`. Blocks when any resolved address is not public.
  - Calls `requests.request(method, url, headers=headers, data=body, timeout=timeout, allow_redirects=False, stream=True)`. Reads at most `MAX_BYTES + 1` bytes.
  - Returns `HttpResult(status=..., ok=2xx)`. A 3xx returns `ok=False` with the status, and the redirect is not followed.

- [ ] **Step 1: Write the failing tests**. Patch `agents.api_tool.socket.getaddrinfo` and `orders.http.requests.request`:
  - `test_action_internal_url_blocked`: `http://127.0.0.1/x`, `http://10.0.0.5/x` and `http://169.254.169.254/` raise `OutboundError`, and `request` is never called.
  - `test_redirect_not_followed`: a 302 returns `ok=False`, and `allow_redirects` is `False`.
  - `test_body_capped`: a 50,000-byte response is truncated with the marker.
  - `test_timeout_is_outbound_error`.
  - `test_non_http_scheme_rejected`: `file://` and `ftp://` are rejected.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** with the same `_is_public_host` logic as `agents/api_tool.py:42-53`. Do not change `agents/api_tool.py`. Import its helpers, as `channels_app/webhooks.py` already does.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): outbound HTTP helper with public-host checks`

---

### Task C3: Action template renderer

**Files:**
- Create: `backend/orders/templates.py`
- Create: `backend/orders/tests/test_templates.py`

**Interfaces:**
- `TemplateError(Exception)`: a missing or unknown placeholder. Fails one order only.
- `ALLOWED_PLACEHOLDERS`: `order.number`, `order.total`, `order.subtotal`, `order.zone_charge`, `order.advance_paid`, `order.collect_amount`, `order.status`, `order.tracking_number`, `order.currency`, `order.items`, `customer.name`, `customer.phone`, `customer.address`, `customer.note`, `zone.name`.
- `build_context(order) -> dict`: returns values keyed by the placeholder names above. `order.items` is a list of `{"name", "external_id", "sku", "variant", "quantity", "unit_price", "product_url"}`.
- `render(template: str, context: dict, mode: str) -> str`. `mode` is `"json"` or `"url"`.
  - Placeholder syntax: `{{ name }}`, matched with `\{\{\s*([A-Za-z_][\w.]*)\s*\}\}`.
  - `json` mode: each value is `json.dumps(value, ensure_ascii=False)`, inserted bare (no surrounding quotes in the template). So `"name": {{ customer.name }}` is correct and `"name": "{{ customer.name }}"` is invalid after rendering.
  - `url` mode: scalar values use `urllib.parse.quote(str(value), safe="")`. `order.items` in URL mode raises `TemplateError`.
  - Unknown placeholder name raises `TemplateError` with code `unknown_placeholder`. A missing value raises `TemplateError` with code `missing_value`.
- `validate_json_template(template: str) -> None`: renders against a sample context and runs `json.loads`. Raises `TemplateError` with code `invalid_body_template` if it fails.

- [ ] **Step 1: Write the failing tests**:
  - `test_json_value_with_quotes_and_newline_is_valid_json`: a `customer.address` of `He said "hi"\nRoad 5` renders in a JSON body and `json.loads` succeeds, giving back the same string.
  - `test_url_value_encoded`: `customer.name` "Rahim & Co" becomes `Rahim%20%26%20Co`.
  - `test_quoted_placeholder_rejected`: a template with `"{{ customer.name }}"` fails `validate_json_template`.
  - `test_unknown_placeholder_rejected`.
  - `test_missing_value_raises_missing_value`.
  - `test_items_as_json_array`: `order.items` renders as a valid JSON array with `external_id` and `sku`.
  - `test_items_in_url_rejected`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** with `re.sub` and a function per mode. No Jinja or string formatting of user values.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): action templates encoded per context`

---

### Task C4: Action configuration API

**Files:**
- Create: `backend/orders/action_config.py`
- Modify: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/tests/test_action_api.py`

**Interfaces:**
- `clean_headers(incoming: list[dict], existing: list[dict]) -> list[dict]`: a blank `value` on an existing header name keeps the stored value. Name must match `^[A-Za-z0-9-]{1,64}$`. Reserved names rejected: `Host`, `Content-Length`, `Transfer-Encoding`, `Connection`. At most 5 headers. Values at most 500 characters.
- `validate_action(data, existing) -> dict`:
  - `url` must parse with `_parse_target`. An IP literal that is not public is rejected with `internal_url`. (DNS is checked at run time by `safe_request`.)
  - `method` `GET` takes no body. `POST` body must pass `validate_json_template`.
  - `response_mapping` keys limited to `tracking_number`. Value is a dotted path such as `data.tracking`, or `data.items.0.code`.
  - `success_status` is blank or one of the order statuses. `success_codes` are ints in 200 to 299.
  - `auto_run` may be True only when `is_active` is True.
- Endpoints (admin for writes, member for reads):
  - `GET`, `POST /api/orders/actions/`.
  - `GET`, `PATCH`, `DELETE /api/orders/actions/<id>/`.
- Response shape for headers: `[{"name", "has_value": true}]`. Values are never returned.

- [ ] **Step 1: Write the failing tests**:
  - `test_headers_write_only`: the GET response body does not contain the secret string, and `has_value` is True.
  - `test_blank_value_keeps_secret_on_edit`.
  - `test_reserved_header_rejected`.
  - `test_internal_ip_url_rejected` (`http://192.168.1.10/hook`) with `internal_url`.
  - `test_invalid_json_body_rejected` with `invalid_body_template`.
  - `test_auto_run_requires_active`.
  - `test_member_cannot_create` (403).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): custom action configuration with write-only headers`

---

### Task C5: Run engine (one request per order)

**Files:**
- Create: `backend/orders/actions.py`
- Modify: `backend/orders/tasks.py` (add `run_action_run(run_id)` Celery task)
- Modify: `backend/orders/api.py` (run, progress, retry, test endpoints)
- Create: `backend/orders/tests/test_action_runs.py`

**Interfaces:**
- `run_action_for_orders(action, orders, actor, force: bool = False) -> dict`:
  - For each order, if `force` is False and a `succeeded` run already exists for `(action, order)`, record that order as `already_sent` and create no run.
  - Otherwise create an `OrderActionRun(status="queued")` and, on commit, enqueue `run_action_run.delay(run.id)`.
  - Returns `{"queued": [run ids], "already_sent": [order numbers]}`.
- `run_action_run(run_id)`:
  1. Mark `running`.
  2. Render URL and body with `render(..., mode)`. A `TemplateError` marks the run `failed` with the error text. Other orders are unaffected.
  3. `safe_request(...)` with the action's headers. An `OutboundError` marks it `failed`.
  4. If `http_status` is in `success_codes`: apply `response_mapping["tracking_number"]` to `order.tracking_number`. If `success_status` is set, call `set_status(order, success_status, actor=("system", action.name))`. Mark `succeeded`.
  5. Otherwise mark `failed` with `http_status` and the redacted excerpt.
  6. Redact every header value from `error` and `response_excerpt` (replace with `***`).
- Endpoints:
  - `POST /api/orders/actions/<id>/run/` (member, `{order_ids, force}`). Returns 409 `already_sent` with the order numbers when not forced and some were already sent.
  - `GET /api/orders/actions/<id>/progress/` returns counts per status.
  - `GET /api/orders/actions/runs/?order_id=` returns run history.
  - `POST /api/orders/actions/runs/<id>/retry/` creates a new run, `attempt = previous + 1`.
  - `POST /api/orders/actions/<id>/test/` (member, `{order_number}`) runs one request synchronously and returns `{ok, http_status, error, response_excerpt}`.

- [ ] **Step 1: Write the failing tests** with `safe_request` patched:
  - `test_action_run_maps_tracking_and_status`: a 200 with `{"data": {"tracking": "T123"}}` sets `tracking_number="T123"` and status `shipped` with a history row.
  - `test_one_failure_does_not_stop_others`: three orders, the second returns 500. Runs 1 and 3 succeed, run 2 is `failed`. This is Review Focus item 3.
  - `test_run_error_redacts_secret`: a response echoing the header value does not contain it in `response_excerpt`. Review Focus item 2.
  - `test_already_sent_needs_force`: the second run without force returns `already_sent` and creates no run. Review Focus item 5.
  - `test_missing_placeholder_fails_only_that_order`.
  - `test_retry_increments_attempt`.
  - `test_test_endpoint_runs_once_and_returns_result`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Each run is its own Celery task, so one failure cannot stop another. Use `transaction.on_commit` before enqueueing.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): run custom actions per order with logs and retry`

---

### Task C6: Order hooks (automatic actions, email and POST integration)

**Files:**
- Modify: `backend/orders/hooks.py` (fill in `order_submitted` and `order_status_changed`)
- Create: `backend/orders/notify.py` (`notify_new_order`, `order_payload`)
- Create: `backend/orders/tests/test_hooks.py`

**Interfaces:**
- `order_payload(order) -> dict`: `{"order": {id, number, status, currency, channel, subtotal, zone_charge, total, advance_paid, collect_amount, tracking_number, created_at, submitted_at}, "customer": {name, phone, address, zone, note, custom}, "items": [{product_id, external_id, sku, name, variant, quantity, unit_price, product_url}]}`.
- `notify_new_order(order) -> None`: `queue_email(to, subject, body)` for each address in `settings.notification_emails`. Subject `New order #<number> · <project name>`. Body is plain text: customer, phone, address, zone, lines as `qty x name (key) @ price`, total, collect amount.
- `order_submitted(order_id)`: if `order.is_test`, return immediately. Otherwise call `notify_new_order`, then `emit_event(order.project, "order.created", order_payload(order))`, then, for each active action with `auto_run=True`, call `run_action_for_orders(action, [order], actor=("system", "auto"))`.
- `order_status_changed(order_id, from_status, to_status)`: if `is_test`, return. Otherwise `emit_event(project, "order.status_changed", {"order": ..., "from", "to"})`.

- [ ] **Step 1: Write the failing tests**:
  - `test_test_order_triggers_nothing`: with `is_test=True`, `queue_email`, `emit_event` and `run_action_for_orders` are never called. Review Focus item 4.
  - `test_new_order_emails_each_address`.
  - `test_new_order_emitted_as_order_created` (`emit_event` called with `"order.created"` and a payload whose items include `external_id` and `sku`).
  - `test_auto_run_only_for_auto_actions`.
  - `test_status_change_emits_event`.
  - `test_payload_contains_product_url`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** The POST integration is the existing `ProjectWebhook`. Do not add a second mechanism. The payload is the fixed `order_payload` shape.

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): email, POST integration and auto-run on new orders`

---

### Task C7: Notification settings API

**Files:**
- Modify: `backend/orders/api.py`, `backend/orders/urls.py`
- Create: `backend/orders/tests/test_notification_settings.py`

**Interfaces:**
- The "Send new orders to a URL" integration is the project's existing `ProjectWebhook`, read and written through the existing `/api/channels/webhook` endpoints. No new model.
- `GET /api/orders/notifications/?project_id=` returns `{"emails": [...], "webhook": {"url", "is_active", "has_secret": true}}`. The webhook signing secret is not returned here; the existing webhook endpoint already handles it.
- `PATCH /api/orders/notifications/` (admin) updates `notification_emails` (valid addresses, at most 10).
- Document in the code comment: the webhook URL receives every project event (`conversation.handoff`, `message.staff_reply` and the order events), and receivers filter on the `event` field.

- [ ] **Step 1: Write the failing tests**: `test_notification_emails_validated`, `test_webhook_summary_has_no_secret`, `test_member_cannot_patch`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit.** `feat(orders): notification settings`

---

### Task C8: Live lookup

**Files:**
- Create: `backend/orders/live.py`
- Modify: `backend/orders/pricing.py` (change the body of `quotes_for` only; the signature stays the same)
- Modify: `backend/orders/api.py` (`GET` and `PATCH` of `live_lookup` under the settings endpoint, admin for PATCH)
- Create: `backend/orders/tests/test_live_lookup.py`

**Interfaces:**
- `lookup_quote(product, fresh: bool) -> Quote`:
  - Only runs when `live_lookup.enabled` and the product has an `external_id` or `sku`.
  - URL built with `render(url_template, {"product": {"external_id", "sku"}}, mode="url")`.
  - Headers from `live_lookup.headers`. Values are write-only.
  - `safe_request("GET", url, headers)`. Parse JSON. `price_path` gives the price. `available_path`, if set, gives availability. Otherwise catalog availability is used.
  - Cache key `live-quote:<project_id>:<product_id>`, TTL 300 seconds. Skipped when `fresh=True`.
  - On `OutboundError`, a timeout or non-JSON response: log with `logger.warning` (no URL query, no header values) and return the catalog quote.
  - A price that is missing or unparseable returns `Quote(price=None)`. That product is not sellable.
- `quotes_for(products, fresh=False)` keeps Plan B's signature. Each product goes to `lookup_quote` when live lookup is enabled, otherwise to the catalog quote from Plan B.
- Live lookup settings are saved through the settings API. Header values follow the same write-only rule as Task C4.

- [ ] **Step 1: Write the failing tests** with `safe_request` patched:
  - `test_live_price_used_when_enabled`.
  - `test_live_lookup_internal_url_blocked`: a `url_template` resolving to a private address falls back to the catalog and logs. Review Focus item 1.
  - `test_failure_falls_back_to_catalog_and_logs`.
  - `test_no_price_from_live_never_sellable`: `Quote(price=None)` and the product is not offered by `find_products`.
  - `test_cache_used_unless_fresh`: a second call hits the cache; `fresh=True` makes a new request.
  - `test_submit_is_always_fresh`: `submit_order` calls `quotes_for(..., fresh=True)`.
  - `test_url_template_encodes_external_id`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Plan B's tests must still pass, because the catalog path is unchanged when live lookup is off.

- [ ] **Step 4: Run tests to verify they pass.** Also run `orders.tests.test_service` and `orders.tests.test_tools`.

- [ ] **Step 5: Commit.** `feat(orders): live price and availability lookup with cache`

---

### Task C9: Backend integration checks

**Files:**
- Create: `backend/orders/tests/test_integration_outbound.py`

- [ ] **Step 1: Write the end-to-end tests** with the mocked HTTP layer:
  - `test_submit_to_action_to_tracking`: submit an order through `submit_order`, with an auto-run action, and assert the tracking number is set and the status changed.
  - `test_secrets_never_in_api_responses`: GET of actions, live lookup and notifications contains no header value.
  - `test_outbound_logs_have_no_secret`: capture logs during a failing run; no header value appears.

- [ ] **Step 2: Run to verify.** `docker compose run --rm django python manage.py test orders -v 2`.

- [ ] **Step 3: Commit.** `test(orders): end-to-end outbound checks`

---

### Task C10: Frontend, notifications and live lookup settings

**Files:**
- Create: `frontend/components/orders/settings/NotificationsSection.tsx` (emails editor; "Send new orders to a URL" uses the existing `webhookApi` from `lib/api.ts`, lines 899-920, with the endpoint field, enabled checkbox, and a note that all project events are sent)
- Create: `frontend/components/orders/settings/LiveLookupSection.tsx` (advanced: URL template with a `{{product.external_id}}` hint, headers with write-only values, price path, availability path, enabled switch)
- Modify: `frontend/app/(dashboard)/project-settings/orders/page.tsx` (add both sections)
- Modify: `frontend/lib/api.ts` (`orderApi` notifications and live lookup functions)

- [ ] **Step 1: Implement.** Header values are shown as "Saved" with a replace button, never pre-filled.
- [ ] **Step 2: Verify.** `npx tsc --noEmit && npm run lint`.
- [ ] **Step 3: Commit.** `feat(frontend): notification and live lookup settings`

---

### Task C11: Frontend, actions editor, run action and run log

**Files:**
- Create: `frontend/components/orders/actions/ActionsSection.tsx` (list), `ActionEditor.tsx` (method, URL, headers, body template with placeholder list, response mapping, success status, auto-run switch, test with order #N)
- Create: `frontend/components/orders/actions/RunActionMenu.tsx`
- Modify: `frontend/components/orders/ActionBar.tsx` (add "Run action ▾" menu from Plan B, which currently has only Set status)
- Modify: `frontend/components/orders/OrderDrawer.tsx` (action log with retry)
- Modify: `frontend/app/(dashboard)/project-settings/orders/page.tsx` (add Actions)
- Modify: `frontend/lib/api.ts` (actions, runs, progress, retry, test functions)

**Behaviour:**
- Run action on selected orders shows progress from the progress endpoint.
- If the response says `already_sent`, show the list of order numbers and ask before resending. Confirm sends `force: true`.
- The drawer's action log shows each run with status and the redacted excerpt, and a retry button for failed runs.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify.** `npx tsc --noEmit && npm run lint`. Check the progress and the confirm-resend dialog by hand.
- [ ] **Step 3: Commit.** `feat(frontend): custom order actions and run log`

---

### Task C12: Final review

- [ ] **Step 1: Run the full test set.** `docker compose run --rm django python manage.py test orders knowledge channels_app agents accounts -v 2`.
- [ ] **Step 2: Run the frontend checks.** `cd frontend && npx tsc --noEmit && npm run lint`.
- [ ] **Step 3: Check the Review Focus list.** Every named test exists and passes.
- [ ] **Step 4: Commit any fixes** with `fix(orders): ...` messages.

---

## Done when

- Every Review Focus test passes.
- A new order from a real channel emails the notification addresses, posts `order.created` to the project webhook, and runs auto actions.
- A test order does none of those.
- A bulk action run continues past one failing order and shows a per-order result.
- No header value, webhook secret or API-tool secret appears in any API response, run record, or log line.
