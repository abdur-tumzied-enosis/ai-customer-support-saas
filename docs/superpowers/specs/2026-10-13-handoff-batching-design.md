# Human Handoff, Message Batching and Outbound Webhooks — Design

Date: 2026-10-13
Status: Approved in brainstorming, pending spec review
Branch: `feature/handoff-batching` (built on `feature/api-call-tool`)

## Goal

1. **Human handoff** — the bot steps back when a person is needed, tells the customer, pauses itself for that conversation, and lets staff take over.
2. **Message batching** — a quick burst of customer messages gets one reply that covers all of them.
3. **Staff replies from our dashboard** — staff can answer handed-off conversations on every channel we support.
4. **Outbound webhooks** — third-party systems are notified of handoff events.

All four are **channel-agnostic**: built once in a shared reply pipeline that each channel plugs into.

## Channels

| Channel | Today | In this project |
|---|---|---|
| Facebook Messenger | Working | Handoff, batching, dashboard staff replies (via Messenger send), webhooks |
| Organic (`self`; dashboard chat today; website widget and public API in future) | Working (dashboard) | Handoff, staff replies, webhooks; batching delay 0 (see §3) |
| WhatsApp, Instagram | Credentials only, no messaging | Out of scope — next project plugs into the same pipeline |

Future organic surfaces (website widget, public chat API) are separate projects; they read staff/bot replies from the same `Message` rows and therefore get handoff, staff replies and webhooks without further work.

## Decisions

| Topic | Decision |
|---|---|
| Where handoff state lives | On `Conversation` (dashboard users share one `self` contact across many conversations) |
| Facebook threads | Every Facebook contact gets a `Conversation` (created by the webhook on first message) |
| Who answers after handoff | Staff, from our dashboard (all channels) or the Meta inbox (Facebook) |
| Resume | "Resume bot" button, or automatically `HANDOFF_AUTO_RESUME_HOURS` (default 24) after the last handoff/staff activity |
| Triggers | Always-on `escalate_to_human` tool + default triggers in the system prompt (overridable by custom instructions) + an English keyword rule in code |
| Staff reply while bot active | Pauses the bot (reason "staff took over") |
| Notifications | Outbound webhooks only (Zapier/Make can forward to Slack/email) |

## 1. Data model

### `Conversation` (channels_app)
- `handoff_at = DateTimeField(null=True, blank=True)` — set ⇒ bot paused (until expiry).
- `handoff_reason = CharField(max_length=255, blank=True, default="")`.
- Helper `is_paused(now=None) -> bool`: `handoff_at is not None and now - handoff_at < timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS)`.
- `channel` (read-only property): `contact.platform` if contact else `"self"`.

### `CustomerContact`
- `display_name = CharField(max_length=255, blank=True, default="")` — best-effort from the Graph API (`GET /{psid}?fields=name`, page token) when the Facebook conversation is created. Failure ⇒ stays blank, logged. Used as the conversation name and shown in the dashboard so staff can find the person in the Meta inbox.

### `Message`
- `sender = CharField(max_length=16, choices=[customer, bot, staff], default="customer")`.
- Migration backfills existing rows: `direction="in"` → `customer`, `direction="out"` → `bot`.
- LLM history maps `customer` → `user`, `bot` and `staff` → `assistant`.

### `ProjectWebhook` (new, channels_app)
- `project = OneToOneField(Project)`, `url = URLField(blank=True)`, `secret = CharField(max_length=64)` (generated with `secrets.token_hex(32)` on create), `is_active = BooleanField(default=False)`, `created_at`, `updated_at`.

### Settings (`core/settings.py`, env-overridable)
- `HANDOFF_AUTO_RESUME_HOURS = 24`
- `HANDOFF_MESSAGE = "I've passed this to our team — a person will reply here soon."`
- `REPLY_DEBOUNCE_SECONDS = {"facebook": 3}` (any channel not listed: 0)

### Facebook conversation creation and history
- `handle_facebook_message` gets or creates the contact's `Conversation` (`create_conversation()` service; knowledge base from the channel credential; name from `display_name` or `"Facebook user"`) and passes it to `create_inbound_message`.
- No data migration for old Facebook messages with `conversation=None`. `build_history` includes `conversation=<this>` **or** `contact=<this contact>, conversation__isnull=True`.

## 2. Reply pipeline (`backend/channels_app/reply.py`)

### Entry point
`schedule_reply(msg: Message) -> None` — called by the Facebook webhook and the dashboard "send message" endpoint. Enqueues `process_reply.apply_async(args=[msg.conversation_id, msg.id], countdown=REPLY_DEBOUNCE_SECONDS.get(channel, 0))`.

### `process_reply(conversation_id, message_id)` — Celery task (bind, max_retries=2)
In order:

1. **Superseded?** If a newer `direction="in"` message exists in the conversation, mark this message `completed` and stop (the newer message's task answers).
2. **Paused?** If `conversation.is_paused()`: mark all pending inbound messages since the last outbound `completed`; send nothing; stop. Staff will answer.
3. **Collect the turn.** All inbound messages since the last outbound message (any sender), oldest first, each formatted with `format_message_with_context`, joined with `"\n"`. History = messages before the first of these (existing `build_history` logic, limit 10).
4. **Keyword rule.** If the turn matches `HANDOFF_PATTERN` (case-insensitive), hand off with reason `"customer asked for a person"` without calling the LLM.
   - Pattern: `\b(human|real person|live (agent|person)|customer (service|support)|operator|talk to (a |an )?(someone|somebody|person|agent|representative|manager))\b`
   - Must not match "my travel agent said…" or "what are your hours". Matching "human resources" is an accepted false positive (§6).
5. **Agent.** Otherwise run the agent (existing `generate_agent_reply`) with an always-on `escalate_to_human(reason: str)` tool. The tool records the reason in `context.handoff_reason` and returns `"Handed off. Tell the customer a person will follow up."`. System prompt gains a HANDOFF section listing default triggers: customer asks for a person; customer is angry or abusive; refund, complaint, cancellation or legal matter; you could not answer twice in a row. Custom instructions may add or override triggers.
6. **On handoff** (rule or tool): set `handoff_at=now`, `handoff_reason`, save; reply text = `HANDOFF_MESSAGE` (replaces any LLM text); emit `conversation.handoff` webhook (§4).
7. **Persist + send.** Create the outbound message (`sender="bot"`, `meta["api_calls"]` as today, the newest inbound message's `agent_reply_id` as id when set). Mark all inbound messages of the turn `completed`. Deliver via the channel sender.

Existing behaviour preserved from `handle_incoming_message`: retry unless a POST API call was already made; after retries, `FALLBACK_REPLY`; Facebook credential lookup by page id.

### Senders (`SENDERS: dict[str, Callable[[Message], dict]]`)
- `facebook` — `send_text_message` with the page credential; stores `external_message_id` and merges the response into `meta`.
- `self` — no-op (the outbound row is the delivery; future widget/API read it).
- Unknown channel — log error, no send.

### Compatibility
`handle_incoming_message` and `handle_ui_message` remain as Celery tasks that call `process_reply` for their message (so messages queued before deploy still get answered). New code never enqueues them.

## 3. Batching rules per channel

- Facebook: 3 s debounce.
- Organic (`self`): 0 s. The dashboard input already waits for each reply; the future widget/API projects choose their own value.
- Superseded messages end `completed` with no `agent_reply_id` of their own. The dashboard treats `completed` without a reply as answered (§5).

## 4. Staff replies, handoff/resume endpoints and webhooks

### Endpoints (`/api/channels/`, project-member auth as existing conversation endpoints)
- `POST conversations/<id>/handoff` — body `{reason?}`; pauses (reason default "paused by staff"); emits `conversation.handoff`.
- `POST conversations/<id>/resume` — clears `handoff_at`/`handoff_reason`; emits `conversation.resumed`.
- `POST conversations/<id>/staff-reply` — body `{text}` (1–2000 chars). Creates `Message(direction="out", sender="staff", status="completed")`, sets `handoff_at=now` (pauses if not paused, reason "staff took over"; refreshes auto-resume), delivers via the channel sender, emits `message.staff_reply`.
  - Facebook: if the customer's last inbound message is older than 24 h, return 400 `outside_messaging_window` and do not save (Meta standard messaging window).
  - Send failure: message saved with `meta["send_error"]`, response 502 `send_failed` so the UI can show it.
- Existing `POST conversations/<id>/messages` (customer-role test message) returns 400 `not_a_test_conversation` for non-`self` conversations.
- `GET/PUT webhook` (project query param) — returns `{url, is_active, secret}`; `PUT {url, is_active}`; `POST webhook/rotate-secret`; `POST webhook/test` sends a `ping` event and returns the HTTP status received.
- Conversation list/detail responses gain `channel`, `handoff_at`, `handoff_reason`, `is_paused`, `resumes_at`, `contact_display_name`; list accepts `?needs_human=1` and orders paused conversations first. Messages gain `sender`.

### Outbound webhooks (`backend/channels_app/webhooks.py`)
- `emit_event(project, event: str, data: dict)` → if the project's webhook is active with a URL, enqueue `deliver_webhook` (Celery, 3 retries with 10 s / 60 s / 300 s backoff on network error or 5xx).
- Body (JSON): `{"id": <uuid>, "event": "...", "created_at": <ISO8601>, "project_id": ..., "data": {...}}`.
- Headers: `Content-Type: application/json`, `X-AMP-Event`, `X-AMP-Delivery` (= id), `X-AMP-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>`.
- `data` for conversation events: `{conversation_id, channel, reason, contact: {id, platform, external_id, display_name}}`; `message.staff_reply` adds `message: {id, text, created_at}`.
- URL safety: `http`/`https` only, host must resolve to public addresses (reuse `agents.api_tool._parse_target` and `_is_public_host`), no redirects, 10 s timeout. Validated on save and on each delivery.

## 5. Dashboard UI

- **Conversations list** (`app/(dashboard)/conversations/page.tsx`): channel badge (Facebook / Test); paused rows show red **Needs human** badge, reason and relative time, sorted first; "Needs human" filter chip.
- **Sidebar**: count badge on Conversations = number of paused conversations (from list endpoint `needs_human_count`; refreshed on page navigation).
- **Conversation page** (`app/(dashboard)/conversations/[id]/page.tsx`):
  - Paused banner: "Bot paused — <reason>. Auto-resumes in <N>h." + **Resume bot** button. Active: **Hand off** button.
  - **Staff reply box** (all channels) below messages, labelled "Reply as staff"; shows API errors (e.g. outside 24 h window).
  - Facebook conversations: customer-role test input hidden; staff reply box shown.
  - Test (`self`) conversations: test input stays; while paused, test messages get no bot reply (same as real channels).
  - Messages show sender: staff messages styled distinctly ("Staff").
  - Polling fix: a message `completed` without `agent_reply` stops polling.
- **Integrations card** on the channels page: webhook URL, active toggle, secret (reveal + copy + rotate), "Send test event" with result.

## 6. Error handling

- Graph name lookup failure → blank name, logged.
- Handoff message send failure → logged like any send failure; pause remains.
- Webhook delivery failure after retries → logged with event id; never blocks replies.
- Keyword rule false positives ("human resources") are accepted: handoff is cheap to undo (Resume). False negatives are covered by the LLM tool.

## 7. Testing

Backend (Django tests, one file per area):
- `channels_app/tests/test_handoff_rules.py`: keyword hits (`"can I talk to a person"`, `"HUMAN please"`, `"customer service"`) and misses (`"my travel agent said"`, `"what are your hours"`); `is_paused` true within window, false after expiry.
- `channels_app/tests/test_reply_pipeline.py` (agent mocked):
  - two quick inbound messages → one agent call whose question contains both texts; older message `completed`, one outbound reply.
  - paused conversation → no agent call, no send, inbound `completed`.
  - keyword rule → no agent call, handoff set, reply = `HANDOFF_MESSAGE`, `conversation.handoff` emitted.
  - tool handoff → reply replaced by `HANDOFF_MESSAGE`, reason stored.
  - history includes legacy Facebook messages without conversation.
  - existing retry/fallback/no-POST-retry behaviour still holds (port `test_reply_retry`).
- `channels_app/tests/test_handoff_api.py`: handoff/resume/staff-reply endpoints, permissions (non-member 403), Facebook 24 h window 400, staff reply pauses active conversation, test-message endpoint 400 for Facebook conversations, `needs_human` filter.
- `channels_app/tests/test_webhooks.py`: signature verifies with secret; inactive/no-URL → nothing sent; private-address URL rejected on save and delivery; retry on 5xx.
- `channels_app/tests/test_facebook_conversation.py`: webhook creates conversation + display name (Graph mocked), name lookup failure tolerated.

Frontend: `tsc --noEmit`, `next lint`, manual checklist in the plan.

## Out of scope

WhatsApp/Instagram channels; website widget; public chat API; helpdesk integrations; email/Slack notifications (use webhooks); detecting staff replies made in the Meta inbox (echo webhook); non-English keyword rules; Facebook `HUMAN_AGENT` message tag (replies after 24 h).
