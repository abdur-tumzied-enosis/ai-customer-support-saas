# Embeddable AI chat widget: architecture review

Status: milestone 1 implemented on `feat/chat-widget`. Sections 1–3 describe the codebase before it.

**Where milestone 1 differs from this plan:**
- **Iframe app:** vanilla JS (`channels_app/widget_assets/app.js`), not Preact. No build step, and the strict embed CSP holds. Move to Preact if the UI outgrows it.
- **`call_api`:** always off for widget visitors ([tasks.py](backend/channels_app/tasks.py) `build_reply_context`). The `allow_api_tools` opt-in moves to milestone 2.
- **Key rotation:** no `key_version` field. Tokens carry the public key, so rotating it (milestone 2) revokes them.
- **No rows per session:** the contact and conversation are created on the visitor's first message, not when the session starts.
- **Origin check:** no advisory check at session creation, since a script can fake it. CSP `frame-ancestors` and the rate limits do the work.
- **Dashboard preview:** not built; the dashboard origin is not in `frame-ancestors` yet.

## 1. What the codebase has today

Each point below was checked in the code.

**Stack and deployment**
- Django 5 + DRF backend, Postgres, Redis, Qdrant, and Celery workers. One queue (`replies`, `REPLY_WORKER_CONCURRENCY`, default 16) handles customer replies; the other handles ingestion. See [settings.py:109-112](backend/core/settings.py#L109-L112) and [docker-compose.yml](docker-compose.yml).
- The embedding and reranker models run once in two text-embeddings-inference services (`embeddings`, `reranker`), so reply worker processes stay small.
- The backend container runs `manage.py runserver`; the gunicorn line is commented out ([Dockerfile](backend/Dockerfile)). The frontend service is commented out in compose. `docker-compose.prod.yml` only contains Qdrant. **There is no production web, proxy or TLS setup in the repo.**
- `uvicorn` is installed but unused. There is no django-channels and no websocket server; `frontend/lib/ws.ts` is never wired to anything.
- Settings defaults to watch: `DEBUG=True`, `SECRET_KEY="dev-secret-key"`, and `ALLOWED_HOSTS` includes `"*"` ([settings.py:8-12](backend/core/settings.py#L8-L12)).

**Auth and tenancy**
- DRF uses `SessionAuthentication` + CSRF, and every view defaults to `IsAuthenticated`.
- The tenant is a `Project`, and users join it through `ProjectUser`. Views check access with `_get_project` / `_verify_project_access` ([api.py:178-192](backend/channels_app/api.py#L178-L192)).
- CORS is a fixed localhost list with `CORS_ALLOW_CREDENTIALS=True`. `XFrameOptionsMiddleware` is on, so Django currently refuses to be framed.
- **No rate limiting exists anywhere.** `Plan.monthly_token_quota` is defined but never enforced.

**Conversations**
- `CustomerContact` is unique on `(project, platform, external_id)`, and `platform` is a free-text field.
- `Conversation.channel` is derived from `contact.platform`.
- Non-`self` contacts have exactly one conversation, through `ensure_contact_conversation`. That function also switches the conversation to the channel's current KB.

**Reply pipeline**
- [reply.py](backend/channels_app/reply.py) works for any channel: `schedule_reply` → `process_reply`. It handles batching, a Redis lock, handoff, retries, fallback text, and delivery through the `SENDERS` table.
- `"self"` already means "the stored row is the delivery", with a comment that literally says *"future widget/API"*.
- `recover_stuck_replies` re-queues `pending` inbound messages that got stuck.
- Contact memory extraction is debounced: one run per contact per `MEMORY_UPDATE_DELAY_SECONDS` (default 300s), covering the whole burst.

**AI**
- `generate_reply` runs a search before the agent, then calls `agent.invoke` (LangChain `create_agent`, Gemini or OpenRouter). **Replies are not streamed.**
- The agent's tools are `retrieve_business_context`, `escalate_to_human`, and `call_api`. `call_api` has SSRF guards and injects secrets server-side.

**Instructions**
- `CustomInstruction.effective(project, kb)` returns the shared instructions plus the KB's own. Instructions can carry `api_domains` and secrets.

**Dashboard test chat**
- It creates a `pending` inbound message, then polls `GET messages/<id>/status` every 1.5s ([conversations/[id]/page.tsx:220](frontend/app/(dashboard)/conversations/[id]/page.tsx#L220)).
- Staff replies and handoff work for any channel that has a `SENDERS` entry.

## 2. Comparing the three approaches

| | **Loader + hosted iframe** | **Render directly on the host page** (Shadow DOM) | **Framework SDK** (npm, e.g. React) |
|---|---|---|---|
| Security | Strongest. Session token and DOM sit on our origin, out of reach of the host's JS. Browsers enforce allowed domains via CSP `frame-ancestors`. | Weak. Host JS can read the token and patch `fetch`; our bugs run with the host's privileges. Needs CORS opened per customer domain. | Same as direct rendering. |
| Integration effort | One `<script>` tag that works on any platform | One `<script>` tag | Only React developers; useless on WordPress, Shopify or Wix |
| Performance | Extra document load (~1 request), loaded lazily | Lightest | Light, but bundled into the customer's app |
| CSS isolation | Complete in both directions | Partial: inherited properties, `rem` sizes and fonts leak through the shadow root | None or partial |
| Updates without reinstalling | Yes; we deploy centrally | Yes | **No**: customers pin a version |
| Maintenance | One codebase on our origin | We must test against hostile host CSS and JS | One package per framework |
| Future actions | A clean, narrow postMessage bridge | Easy, but no trust boundary | Easy, but no trust boundary |

**Recommendation: a small loader script plus a hosted iframe served by the Django backend.**
- The cost is iframe overhead: resize handling, mobile full-screen, focus management, and browser storage partitioning.
- Add a React SDK later as a thin wrapper around the loader, not as its own renderer.

## 3. Architecture and data flow

```
Host page                         Our backend origin (Django)
─────────                         ──────────────────────────
<script src=/widget.js            GET /widget.js           (static, short cache)
  data-widget-key=wgt_…>
 loader (vanilla, ~3KB) ──iframe─▶ GET /embed/<key>/        HTML shell + inline public config
   │  postMessage (origin-        │   CSP: frame-ancestors <allowed_origins> <dashboard>
   │  checked, versioned)         │   X-Frame-Options exempt for this view only
   ▼                              ▼
 resize/open/close  ◀──────────  iframe app (Preact) — same-origin XHR, no CORS, no cookies
                                  POST /api/widget/<key>/session  → signed visitor token
                                  POST /api/widget/messages       → Message(in, pending) → schedule_reply
                                  GET  /api/widget/messages?after → new bot/staff msgs + state
                                                │
                                  Celery `replies`: process_reply → generate_reply (RAG, KB, instructions)
                                                → SENDERS["widget"] = stored row (like "self")
```

How it reuses what exists:
- A visitor becomes a `CustomerContact(platform="widget", external_id="<widget_id>:<visitor_uuid>")`.
- `ensure_contact_conversation(contact, widget.knowledge_base)` gives that visitor one conversation, which follows the widget's KB.
- `process_reply`, handoff, staff reply, memory, the conversations inbox and analytics all work unchanged. Adding `"widget"` to `SENDERS` and `REPLY_DEBOUNCE_SECONDS` is enough.

**Why serve the iframe from Django rather than the Next.js app:**
- API calls from the iframe are same-origin, so there is no CORS change and no third-party cookies.
- Each widget can set its own CSP header without a round trip through Next middleware.
- The bundle stays small: Preact rather than the full Next/React runtime.

**Sessions**
- Tokens use `django.core.signing` (built in), carrying `{widget_id, contact_id, key_version}` with a `max_age` of 30 days.
- They are stored in the iframe's `localStorage` and sent as a bearer header.
- Tokens are stateless. They are revoked by disabling the widget or rotating its key.

**Live updates in milestone 1: polling, as the dashboard test chat already does.**
- The iframe polls `GET messages?after=` every ~1s while a reply is pending, every ~10s while the panel is open, and stops when it is closed.
- A single endpoint covers bot replies, staff replies and handoff state.
- Retries are idempotent: each message carries a `client_id` stored in `meta`.

**Streaming tradeoff**
- Server push later means SSE over ASGI (uvicorn is already installed) with Redis pub/sub.
- **Streaming tokens conflicts with existing logic:**
  - `_reply` throws away a generated reply if a newer customer message arrived mid-generation.
  - It replaces the reply text with the handoff message when the agent escalates.
  - With streaming, the visitor would see text that then vanishes.
- So: use push for events first, and do token streaming only after reworking those semantics.

## 4. Database and API changes

**New model `Widget`.** It could live in `channels_app` or a new app. Fields:
- `project`
- `name`
- `public_key` (`wgt_` + `token_urlsafe`, unique, rotatable) and `key_version`
- `knowledge_base` (FK, `PROTECT`, the same as `Conversation`)
- `is_active`
- `allowed_origins` (JSON list, normalized to `https://host[:port]`, optionally `*.host`)
- `appearance` (JSON: title, welcome message, primary/text colors, position left or right)
- `logo` (file)
- `instructions` (text, applied after the KB's instructions)
- `allow_api_tools` (bool, default **False**)
- `last_seen_origin` and `last_seen_at` (for "installation verified")
- timestamps

**Deferred:** a `Conversation.widget` FK. The widget id is already encoded in `external_id`; add the FK when analytics needs to filter by widget.

**Dashboard API** (session auth, existing `project_slug` pattern):
- `GET/POST /api/channels/widgets`
- `GET/PATCH/DELETE /api/channels/widgets/<id>`
- `POST …/<id>/rotate-key`
- `POST …/<id>/logo`

**Public API** (no session; widget-token auth; DRF throttles):
- `GET /embed/<key>/`
- `POST /api/widget/<key>/session`
- `GET` and `POST /api/widget/messages`

The public config uses an explicit field whitelist. KB ids, instructions and `api_domains` never leave the server.

## 5. Dashboard UX

- Add a **Widgets** item in the sidebar, opening a list page: name, KB, active toggle, last seen.
- The editor has three tabs:
  - **General:** name, KB picker (default KB preselected, and a warning when the KB has no docs, as the Channels page already does), instructions, API tools toggle with a risk note.
  - **Appearance:** color, position, welcome message, logo, with a live preview iframe beside the form. The draft config reaches the preview via postMessage, accepted only from the dashboard origin.
  - **Install:** allowed domains, a copy-snippet button, per-platform guides, and an "Installation detected on X" status.
- In the conversations list, widget conversations get a "Website" badge (the badge currently maps only `self` and `facebook`).

## 6. Security controls

- **No secrets in the browser.** The public key is only an identifier. LLM keys and `call_api` secrets stay on the server, and the config uses a field whitelist.
- **Tenant isolation.**
  - The project comes from the widget, found by its key.
  - The contact and conversation come only from the signed token.
  - Nothing tenant-related is taken from client parameters.
  - Tests cover using widget A's token against widget B.
- **Domain authorization.**
  - The browser enforces it, through CSP `frame-ancestors` on the embed response.
  - Defense in depth: session creation also checks `location.ancestorOrigins` / `document.referrer` against the allowlist. This check is advisory, since Firefox lacks `ancestorOrigins`.
  - **This allowlist cannot stop scripts calling the API outside a browser.** Only rate limits and quotas can.
- **Rate limits.** Use DRF's built-in throttles, backed by the Redis cache that already exists:
  - session creation per IP
  - messages per token
  - messages per widget
  - a maximum message length of 2,000 characters
  - **enforce `monthly_token_quota`**, which today is not enforced
- **postMessage.**
  - The loader checks `event.origin === EMBED_ORIGIN` and `event.source === iframe.contentWindow`.
  - The iframe checks the verified parent origin.
  - Messages are always sent with an explicit `targetOrigin`.
  - Envelope is `{v, type, id}`, and only allowlisted types are accepted.
- **XSS.**
  - Bot and staff text is rendered as text, or as escaped minimal markdown.
  - Links must be http(s) only and open with `noopener`.
  - The embed page sets a strict CSP (`script-src 'self'`).
  - Logos must be PNG, JPEG or WebP under a size cap. **No SVG.** Serve them with `nosniff`.
- **iframe `sandbox`.** Set it without `allow-top-navigation`, so the widget can never navigate the host page.
- **Anonymous visitors and `call_api`.**
  - KB instructions can enable `call_api`, including POST requests.
  - A public widget would expose that to anyone on the internet.
  - Hence `allow_api_tools` defaults to off.
- **Production prerequisites.**
  - Set a real `SECRET_KEY`; signed tokens depend on it.
  - Set `DEBUG=False` and restrict `ALLOWED_HOSTS`.
  - Replace `runserver` with gunicorn or uvicorn behind TLS.

## 7. Platform compatibility

- **Plain HTML and GTM:** paste the snippet.
- **WordPress:** a header/footer plugin or `wp_footer`.
- **Shopify:** `theme.liquid`.
- **Webflow:** custom code.
- **Wix and Squarespace:** code injection on paid plans.
- **Single-page apps:** include the snippet once in the root layout. The loader guards against loading twice and survives route changes.
- **Hosts with a strict CSP** must allow our origin in `script-src` and `frame-src`. The Install tab should document this.
- **Later:** a WordPress plugin, a Shopify app embed, and a React wrapper, all thin layers over the loader.

## 8. Preserving a path for future host-site actions

Nothing below gets built now. It keeps actions possible later without a redesign.

**How actions would work later:**
1. **The owner declares actions in the dashboard:** name, description, JSON schema, and whether confirmation is required.
2. **The agent gets tools generated only from enabled actions.** A tool call does **not** execute. It stores an `action_request` message.
3. **The iframe shows a confirmation UI.** The host page cannot spoof it, because it lives inside our iframe.
4. **On approval, the iframe sends a postMessage to the verified parent origin.**
5. **The loader dispatches only to handlers the host explicitly registered** with `AutoMagic('registerAction', name, fn)`.
6. **The result returns** loader → iframe → API → agent, and every step is audited.

Two allowlists must both say yes: the dashboard enables the action and the host registers it. There is no eval and no generic DOM access.

**Keep these in milestone 1 so that path stays open:**
- the versioned postMessage envelope
- the loader as the only code that touches the host page
- the command-queue API (`window.AutoMagic(...)`)
- message kinds carried in `Message.meta`, so no schema change is needed later

## 9. Testing

**Backend** (Django tests, in the existing `tests/` style):
- dashboard CRUD tenant isolation, plus malformed ids returning 404, matching commit `eaf9a85`
- origin normalization
- inactive widget → 404
- disallowed origin → 403
- tampered, expired or cross-widget token → 401
- message listing scoped to the visitor's own conversation
- throttling → 429
- the config field whitelist
- the embed CSP header, with X-Frame-Options removed
- `SENDERS["widget"]` delivery
- staff replies on widget conversations
- API tools gated by `allow_api_tools`

**End to end** (Playwright):
- a test page on an allowed origin works
- a disallowed origin gets its iframe blocked
- a page with hostile CSS (`* {all: unset !important}`) does not break the widget
- mobile viewport
- spoofed postMessage from the host page is ignored
- send a message and get a reply (with the LLM mocked)

**Load:** a burst of widget sessions against `replies` worker concurrency and the Gemini quota (expect 429s; check the fallback path).

## 10. Risks

1. **Gemini capacity, not workers, limits replies.** Website traffic spikes harder than Messenger.
   - Pay-as-you-go Vertex uses Dynamic Shared Quota, so bursts get `429 RESOURCE_EXHAUSTED` even on a paid project.
   - Replies with the default thinking level can exceed the 30s client timeout ([llm.py:114](backend/agents/llm.py#L114)) and fail with `504 DEADLINE_EXCEEDED`.
   - In a 9-question test on 2026-10-25, the default level answered 4 of 9; `LLM_THINKING_LEVEL=low` answered 9 of 9 but missed one catalog answer.
   - Mitigations: retries with backoff, `LLM_FALLBACK_MODEL` on a different model, a tuned thinking level, Provisioned Throughput at steady volume.
2. **LLM cost abuse.** There are no rate limits or quota enforcement today. CAPTCHA (e.g. Turnstile) is an optional later layer.
3. **Production server topology is unknown.** There is no proxy, TLS or static-file setup in the repo.
4. **Lost visitor sessions.** Storage partitioning and Safari's tracking prevention can drop them. Falling back to a fresh session is acceptable.
5. **Streaming versus the drop/handoff logic,** as described in section 3.

## 11. Milestones and acceptance criteria

**Milestone 1: working vertical slice (polling).** Contents:
- the `Widget` model and migration
- the dashboard CRUD API
- the embed view with per-widget CSP
- the session and messages endpoints with signed tokens
- DRF throttles
- `SENDERS["widget"]`
- the loader and a Preact iframe app (launcher, panel, welcome message, message list, input, pending/typing state, error and retry, mobile full-screen)
- a dashboard Widgets page with General, basic Appearance and Install tabs

Acceptance criteria for milestone 1:
- [ ] A user creates a widget, picks a KB, adds `https://example.test`, and copies a one-line snippet.
- [ ] On an allowed origin the widget renders and answers from the chosen KB. On any other origin the browser refuses to render the iframe.
- [ ] Changing the color or welcome message shows up on the next page load without touching the snippet.
- [ ] Disabling the widget makes the embed return 404 and makes existing tokens fail.
- [ ] A visitor's conversation appears in the dashboard inbox. Handoff and staff replies reach the visitor.
- [ ] A token from widget A cannot read or post to widget B. No KB id, instruction or secret appears in any public response.
- [ ] Excess requests get 429s. Messages over 2,000 characters are rejected.
- [ ] A hostile-CSS host page and a 375px viewport both render correctly.
- [ ] Backend tests and the Playwright test listed above pass.

**Milestone 2:**
- logo upload, live preview, key rotation, per-widget instructions
- installation verification
- `Conversation.widget` FK and per-widget analytics
- enforcing `monthly_token_quota`
- accessibility pass

**Milestone 3:**
- server push over ASGI and Redis pub/sub, replacing polling
- typing events
- token streaming afterwards, once the drop/handoff semantics are reworked

**Milestone 4:**
- CDN for widget assets
- WordPress, Shopify and GTM wrappers, and a React wrapper
- optional Turnstile

**Milestone 5:** host-site actions, as described in section 8.

## Assumptions and open questions

None of these block milestone 1; each has a default I'd go with.
- **Production domain layout:** I assume the backend has a public HTTPS origin (`PUBLIC_BASE_URL` exists) that can serve the embed page and `widget.js`.
- **Widget instructions:** I assume per-widget text that adds to the KB's instructions, rather than a whole new instruction system.
- **Inbox:** I assume widget conversations appear in the existing inbox and use handoff.
- **Visitors are anonymous.** Identifying logged-in visitors (a host-signed user token) belongs in a later milestone.

## Recommended architecture and first milestone

**Architecture:**
- A vanilla loader of about 3KB and a Preact iframe served from the Django origin.
- Allowed domains enforced by the browser through per-widget CSP `frame-ancestors`.
- Stateless signed visitor tokens, no cookies, and same-origin API calls.
- Visitors become `CustomerContact(platform="widget")` and go straight into the existing reply pipeline.

**First milestone:** milestone 1 above, a working widget with polling and its listed acceptance criteria.