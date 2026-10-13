# API Call Tool for the Reply Agent — Design

Date: 2026-10-13
Status: Approved in brainstorming, pending spec review

## Goal

Let a business's AI agent call the business's own HTTP APIs while replying to a customer, so it can:

- **Look up data** — order status, stock, booking slots, account info (GET).
- **Take actions** — create a lead, book an appointment, open a ticket (POST).
- **Notify** — fire a webhook to Zapier / Make / n8n / Slack (POST).

The tool is built in and available to every project, but **disabled by default**. A business enables it per custom instruction and describes, in the instruction text, which URLs to call and when.

## Decisions

| Topic | Decision |
|---|---|
| Config style | Hybrid: instruction has an enable switch + allowed-domain list + optional secret per domain. The LLM builds URL and body itself, constrained to those domains. |
| Where config lives | On `CustomInstruction`; follows the existing effective-instruction rule (KB-level instructions replace project-level ones). |
| POST guard | None in code. Businesses control behaviour via instruction text (e.g. "confirm with the customer before booking"). Code enforces only allowlist, SSRF guard, limits. |
| Schemes | `http://` and `https://` both allowed. UI warns when a secret is used with http. |
| Methods | GET and POST only. |
| Secrets | Stored in the DB JSON (same as `ChannelCredential` today), write-only through the API, injected server-side, never shown to the LLM. Encryption deferred. |

## 1. Data model

Add to `knowledge.models.CustomInstruction` (one migration):

- `api_tool_enabled = BooleanField(default=False)`
- `api_domains = JSONField(default=list, blank=True)` — list of:
  ```json
  {"domain": "shop.com", "auth_header": "Authorization", "secret": "Bearer sk_..."}
  ```
  `auth_header` and `secret` are optional (both or neither).

**Effective tool config:** the tool is registered only if at least one *effective* instruction (as returned by `get_effective_instructions`) has `api_tool_enabled=True` and a non-empty `api_domains`. The allowed set is the union of those instructions' domains. If the same domain appears twice with different secrets, the most recently created instruction wins (matches the existing `-created_at` ordering).

## 2. Backend runtime

### New module `backend/agents/api_tool.py`

`build_call_api_tool(domains: list[dict]) -> Callable` returns a closure exposed to the agent as:

```
call_api(method: str, url: str, body: dict | None = None) -> str
```

Docstring shown to the LLM lists the allowed domains and says to follow the custom instructions for URLs, methods and fields.

Per call, in order:

1. **Call cap** — at most 5 calls per reply (counter in the closure; a new tool is built per reply).
2. **Method** — must be `GET` or `POST` (case-insensitive).
3. **URL** — scheme `http` or `https`; hostname equals an allowed domain or ends with `"." + domain`. `evilshop.com` and `shop.com.evil.io` must not match `shop.com`.
4. **SSRF guard** — resolve hostname (`socket.getaddrinfo`); reject if any resolved address is private, loopback, link-local, reserved, multicast or unspecified (`ipaddress` module). Covers `127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16` (cloud metadata), `::1`, `fc00::/7`, `fe80::/10`.
5. **Auth** — if the matched domain has `auth_header` + `secret`, set that header. The LLM cannot set headers.
6. **Request** — `requests.request(method, url, json=body if POST, params=None, timeout=10, allow_redirects=False, stream=True)`; read at most 20 KB; append `"\n[truncated]"` if more.
7. **Result** — `"HTTP <status>\n<body text>"`. 3xx is returned as-is (not followed).

Errors never raise into the agent; they return `"ERROR: <reason>"` (blocked domain, blocked IP, bad method, call limit reached, timeout, connection error).

Each call appends `{"method", "url", "status", "ms"}` (or `"error"`) to a list held by the tool; secrets are never logged.

### Wiring

- `agents/rag.py`
  - `Context` gains `api_domains: Optional[List[dict]] = None` and `api_call_log: List[dict] = field(default_factory=list)`.
  - `_build_tools_from_context` appends `build_call_api_tool(context.api_domains, log=context.api_call_log)` when `api_domains` is non-empty.
  - `generate_reply` signature and return type are unchanged; the caller reads `context.api_call_log` after it returns.
- `channels_app/tasks.py`
  - `get_effective_instructions` also returns `api_tool_enabled` and `api_domains` per instruction.
  - `generate_agent_reply` builds the allowed-domain union and passes it in `Context`.
  - The outbound message's `meta["api_calls"]` stores the call log (both Facebook and UI paths).
- `agents/llm.py` — no change; the instruction text already flows into the system prompt.

## 3. API (serializer)

`CustomInstructionSerializer` in `knowledge/views.py`:

- Adds `api_tool_enabled` and `api_domains`.
- **Output:** each domain is returned as `{domain, auth_header, has_secret}`; `secret` is never returned.
- **Input validation:**
  - max 10 domains; `domain` required; normalized to a lowercase hostname (strip scheme, path, port); must contain a dot; no IP literals.
  - `auth_header` must be a valid HTTP header token if present; `secret` requires `auth_header`.
  - `api_tool_enabled=True` with empty `api_domains` → 400.
- **Secret preservation on update:** when an incoming domain entry has an empty/missing `secret` and the same domain existed before with a secret, keep the stored secret. To clear a secret, the client removes the `auth_header` too.

Create/update views already go through this serializer; any view that builds instructions manually must pass the new fields through.

## 4. Frontend

`frontend/lib/api.ts`:

```ts
type ApiDomain = { domain: string; auth_header?: string; has_secret?: boolean; secret?: string };
// CustomInstruction gains:
api_tool_enabled: boolean;
api_domains: ApiDomain[];
```

`frontend/app/(dashboard)/knowledge/page.tsx` — add and edit instruction dialogs get a collapsible **"API access"** section below the message:

- Switch → `api_tool_enabled`.
- Rows of `Domain | Auth header | Secret | ✕`, plus "+ Add domain".
- Domain input accepts a pasted URL and is reduced to its hostname client-side.
- Secret is a password input; on edit shows placeholder "(set)" when `has_secret` and sends empty to keep it.
- Save blocked with inline message if switch on and no domains.
- Inline warning when a row has a secret and the message text references that domain with `http://`.
- Helper text: "The bot can make GET/POST calls only to these domains. Use a low-privilege API key. Describe which URLs and fields to use in the message above."
- "Insert example" link appends a sample instruction to the message.
- Instruction list cards show an **API** badge when enabled.

Uses existing UI components (`Input`, `Label`, `Button`, `Badge`, `Dialog`); no new dependencies.

## 5. Error handling

- Tool failures return `ERROR: ...` text to the LLM; the reply pipeline continues and the bot explains it could not complete the action.
- Invalid config is rejected at save time (400), not at reply time.
- Missing/old instructions (no new fields) behave exactly as today: tool absent.

## 6. Security notes

- Customers can try to steer the bot into calling other paths on an allowed domain. The allowlist bounds this to the business's own API; businesses must use scoped, low-privilege keys. This is stated in UI helper text.
- DNS-rebinding window between our resolve and `requests`' resolve is accepted for now. Upgrade path: connect to the resolved IP directly with a `Host` header.
- `http://` exposes secrets and body in transit; allowed per product decision, with UI warning.

## 7. Testing

- `backend/agents/tests/test_api_tool.py` (mock `requests` and `socket.getaddrinfo`):
  - exact domain and subdomain allowed; `evilshop.com`, `shop.com.evil.io` blocked
  - `127.0.0.1`, `10.0.0.5`, `169.254.169.254`, `::1` resolutions blocked
  - secret header attached only for its domain; LLM cannot add headers
  - PUT rejected; redirects not followed; >20 KB truncated; 6th call returns limit error
  - timeout returns `ERROR: timeout`
- `backend/knowledge/tests/test_instruction_api_tool.py`:
  - secret never in response; `has_secret` true when set
  - update with blank secret keeps stored secret
  - enabled with no domains → 400; IP-literal domain → 400
- Frontend: manual check of add/edit/badge flows.

## Out of scope

PUT/DELETE, LLM-supplied headers, retries, response caching, "test call" button, per-domain path allowlists, secret encryption, UI to display `meta["api_calls"]`.
