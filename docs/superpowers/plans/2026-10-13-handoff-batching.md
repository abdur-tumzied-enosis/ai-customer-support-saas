# Human Handoff, Batching and Webhooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Channel-agnostic reply pipeline with message batching, human handoff (keyword rule + agent tool), dashboard staff replies, and signed outbound webhooks — wired to Facebook and organic (`self`) conversations.

**Architecture:** New `channels_app/reply.py` owns `schedule_reply` → `process_reply` (debounce/supersede, pause check, keyword rule, agent, handoff, persist, per-channel sender). `channels_app/handoff.py` holds the keyword rule and pause/resume actions; `channels_app/webhooks.py` signs and delivers events via Celery. Facebook contacts get a `Conversation`, so handoff state lives on `Conversation`. Existing `tasks.handle_incoming_message`/`handle_ui_message` become thin wrappers. Frontend gets handoff/staff-reply controls, list badges, sidebar count and a webhook settings card.

**Tech Stack:** Django 5 + DRF, Celery, LangChain agent (existing), `requests`, `hmac`; Next.js/React/Tailwind with existing `components/ui`.

**Spec:** `docs/superpowers/specs/2026-10-13-handoff-batching-design.md`

## Global Constraints

- Indentation: tabs in all `backend/channels_app/*`, `backend/agents/*` files and new files; match each existing file.
- Settings defaults: `HANDOFF_AUTO_RESUME_HOURS=24`; `HANDOFF_MESSAGE="I've passed this to our team — a person will reply here soon."`; `REPLY_DEBOUNCE_SECONDS={"facebook": 3}` (others 0).
- Keyword rule English only; pattern exactly as in Task 1.
- Webhook headers: `X-AMP-Event`, `X-AMP-Delivery`, `X-AMP-Signature: sha256=<hex>`; retries 3 with countdowns 10/60/300 s; 10 s timeout; no redirects; public addresses only.
- Staff reply text 1–2000 chars; Facebook staff replies only within 24 h of the customer's last inbound message.
- No new Python or npm dependencies.
- Deliberate spec deviation: the spec lists "non-member 403"; existing conversation endpoints reject non-members through `_verify_project_access`, which raises DRF `ValidationError` (HTTP 400). New endpoints reuse it, so non-members get **400** — consistent with the rest of the API.
- Run backend tests per app label (multi-label runs collide on `tests` packages): `docker compose run --rm django python manage.py test <label> -v 2` from repo root.
- Frontend checks: `cd frontend && npx tsc --noEmit && npm run lint`.

## Review Focus

1. A burst where the first message's task starts the LLM before the second arrives → both tasks may include the first message (possible duplicate answer to message 1). Accepted race; test pins the common case (task for older message sees newer → skips). Task 4 test `test_older_message_task_skips_when_newer_exists`.
2. Messages that arrived while paused, then bot resumed → next turn includes them (answered together). Task 4 test `test_messages_during_pause_are_answered_after_resume`.
3. Staff reply on a Facebook conversation whose customer never wrote (no inbound) → 400 outside window, not a crash. Task 6 test `test_staff_reply_facebook_without_inbound_is_rejected`.
4. Webhook configured with a URL that later resolves to a private IP → delivery skipped, no exception. Task 2 test `test_delivery_skipped_when_url_turns_private`.
5. Legacy queued `handle_incoming_message` for a message without a conversation → conversation created, reply scheduled. Task 4 test `test_legacy_incoming_wrapper_creates_conversation`.

---

### Task 1: Data model, settings, keyword rule

**Files:**
- Modify: `backend/channels_app/models.py` (`CustomerContact`, `Conversation`, `Message`; new `ProjectWebhook` at end)
- Create: `backend/channels_app/migrations/0011_handoff_batching.py` (generated + RunPython)
- Modify: `backend/core/settings.py` (after `LLM_MODEL` line)
- Modify: `backend/channels_app/service/message_service.py` (`create_outbound_message` gains `sender`)
- Create: `backend/channels_app/handoff.py`
- Test: `backend/channels_app/tests/test_handoff_rules.py`

**Interfaces:**
- Produces:
  - `Conversation.handoff_at`, `Conversation.handoff_reason`, `Conversation.channel -> str` (property), `Conversation.is_paused(now=None) -> bool`, `Conversation.resumes_at() -> Optional[datetime]`
  - `CustomerContact.display_name: str`
  - `Message.sender: "customer" | "bot" | "staff"`
  - `ProjectWebhook(id: UUID, project: OneToOne, url, secret, is_active)`
  - `create_outbound_message(..., sender: str = "bot")`
  - `channels_app.handoff.HANDOFF_PATTERN`, `wants_human(text: str) -> bool`

- [ ] **Step 1: Write failing tests** — `backend/channels_app/tests/test_handoff_rules.py`:

```python
from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from channels_app.handoff import wants_human
from channels_app.models import Conversation


class WantsHumanTest(SimpleTestCase):
	def test_hits(self):
		for text in (
			"can I talk to a person",
			"HUMAN please",
			"I want customer service",
			"let me speak to someone",
			"is there a real person here?",
			"talk to an agent",
		):
			assert wants_human(text), text

	def test_misses(self):
		for text in ("my travel agent said it was fine", "what are your hours", "", None):
			assert not wants_human(text), text


@override_settings(HANDOFF_AUTO_RESUME_HOURS=24)
class IsPausedTest(SimpleTestCase):
	def test_not_paused_without_handoff(self):
		assert not Conversation().is_paused()

	def test_paused_within_window_and_expires(self):
		now = timezone.now()
		conv = Conversation(handoff_at=now - timedelta(hours=23))
		assert conv.is_paused(now)
		assert conv.resumes_at() == conv.handoff_at + timedelta(hours=24)
		conv.handoff_at = now - timedelta(hours=25)
		assert not conv.is_paused(now)

	def test_channel_defaults_to_self_without_contact(self):
		assert Conversation().channel == "self"
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_handoff_rules -v 2`
Expected: ERROR `ModuleNotFoundError: No module named 'channels_app.handoff'`

- [ ] **Step 3: Settings** — in `backend/core/settings.py` after the `LLM_MODEL = ...` line:

```python
HANDOFF_AUTO_RESUME_HOURS = env.int("HANDOFF_AUTO_RESUME_HOURS", default=24)
HANDOFF_MESSAGE = env("HANDOFF_MESSAGE", default="I've passed this to our team — a person will reply here soon.")
REPLY_DEBOUNCE_SECONDS = {"facebook": env.int("FACEBOOK_REPLY_DEBOUNCE_SECONDS", default=3)}  # channels not listed: 0
```

- [ ] **Step 4: Models** — in `backend/channels_app/models.py`:

Add to the imports at the top:

```python
import secrets
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
```

In `CustomerContact`, after `external_id = ...`:

```python
	display_name = models.CharField(max_length=255, blank=True, default="")
```

In `Conversation`, after the `knowledge_base` field:

```python
	# Human handoff: set => bot paused until resumed or HANDOFF_AUTO_RESUME_HOURS pass
	handoff_at = models.DateTimeField(null=True, blank=True)
	handoff_reason = models.CharField(max_length=255, blank=True, default="")
```

and after `__str__` in `Conversation`:

```python
	@property
	def channel(self) -> str:
		return self.contact.platform if self.contact_id else "self"

	def resumes_at(self):
		return self.handoff_at + timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS) if self.handoff_at else None

	def is_paused(self, now=None) -> bool:
		return self.handoff_at is not None and (now or timezone.now()) < self.resumes_at()
```

In `Message`, after the `direction` field:

```python
	SENDER_CHOICES = [("customer", "Customer"), ("bot", "Bot"), ("staff", "Staff")]
	sender = models.CharField(max_length=16, choices=SENDER_CHOICES, default="customer")
```

At the end of the file:

```python
def _new_webhook_secret() -> str:
	return secrets.token_hex(32)


class ProjectWebhook(models.Model):
	"""Outbound webhook endpoint for a project (handoff/resume/staff-reply events, see channels_app/webhooks.py)."""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="webhook")
	url = models.URLField(blank=True, default="")
	secret = models.CharField(max_length=64, default=_new_webhook_secret)
	is_active = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"{self.project.name} webhook ({'on' if self.is_active else 'off'})"
```

Note: `Conversation.channel` uses `contact_id` then `contact.platform`; `Conversation()` unsaved has `contact_id=None` → `"self"`.

- [ ] **Step 5: `create_outbound_message` sender** — in `backend/channels_app/service/message_service.py`, add parameter `sender: str = "bot",` after `id: Optional[uuid.UUID] = None` in the signature, document it in the docstring Args (`sender: "bot" (default) or "staff"`), and add `"sender": sender,` to `create_kwargs`.

- [ ] **Step 6: Keyword rule** — create `backend/channels_app/handoff.py`:

```python
"""Human handoff: keyword rule and pause/resume actions."""
import re

HANDOFF_PATTERN = re.compile(
	r"\b(human|real person|live (agent|person)|customer (service|support)|operator"
	r"|(talk|speak) to (a |an )?(someone|somebody|person|agent|representative|manager))\b",
	re.IGNORECASE,
)


def wants_human(text) -> bool:
	"""English-only rule; the agent's escalate_to_human tool covers everything else."""
	return bool(HANDOFF_PATTERN.search(text or ""))
```

- [ ] **Step 7: Migration**

Run: `docker compose run --rm django python manage.py makemigrations channels_app -n handoff_batching`
Expected: creates `channels_app/migrations/0011_handoff_batching.py` with AddField ×4 (`display_name`, `handoff_at`, `handoff_reason`, `sender`) and CreateModel `ProjectWebhook`.

Then edit the generated file: add above `class Migration`:

```python
def backfill_sender(apps, schema_editor):
	Message = apps.get_model("channels_app", "Message")
	Message.objects.filter(direction="out").update(sender="bot")  # inbound rows already default to "customer"
```

and append to its `operations` list:

```python
        migrations.RunPython(backfill_sender, migrations.RunPython.noop),
```

(generated migration files use 4-space indentation — keep it).

- [ ] **Step 8: Run, verify pass**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_handoff_rules -v 2`
Expected: 5 tests OK.

- [ ] **Step 9: Commit**

```bash
git add backend/core/settings.py backend/channels_app/models.py backend/channels_app/migrations/0011_handoff_batching.py backend/channels_app/service/message_service.py backend/channels_app/handoff.py backend/channels_app/tests/test_handoff_rules.py
git commit -m "feat(channels): handoff fields, message sender, project webhook model, keyword rule"
```

---

### Task 2: Outbound webhooks module

**Files:**
- Create: `backend/channels_app/webhooks.py`
- Test: `backend/channels_app/tests/test_webhooks.py`

**Interfaces:**
- Consumes: `ProjectWebhook` (Task 1); `agents.api_tool._parse_target(url) -> (scheme, host)` raises `ValueError`; `agents.api_tool._is_public_host(host) -> bool`.
- Produces:
  - `class WebhookUrlError(ValueError)`
  - `validate_webhook_url(url: str) -> str`
  - `sign(secret: str, body: bytes) -> str` (`"sha256=<hex>"`)
  - `build_event(project_id, event: str, data: dict) -> dict`
  - `post_event(hook: ProjectWebhook, payload: dict) -> int` (HTTP status; raises `WebhookUrlError` / `requests.RequestException`)
  - `emit_event(project, event: str, data: dict) -> None`
  - Celery task `deliver_webhook(hook_id: str, payload: dict) -> Optional[int]`

- [ ] **Step 1: Write failing tests** — `backend/channels_app/tests/test_webhooks.py`:

```python
import hashlib
import hmac
import json
import socket
from unittest import mock

from django.test import TestCase

from accounts.models import Project
from channels_app.models import ProjectWebhook
from channels_app.webhooks import (
	WebhookUrlError, build_event, deliver_webhook, emit_event, sign, validate_webhook_url,
)

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]


@mock.patch("agents.api_tool.socket.getaddrinfo", return_value=PUBLIC)
class WebhookTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-hooks")
		self.hook = ProjectWebhook.objects.create(project=self.project, url="https://hooks.example.com/in", is_active=True)

	def test_sign_matches_hmac_sha256(self, _dns):
		body = b'{"a":1}'
		expected = hmac.new(b"s3", body, hashlib.sha256).hexdigest()
		assert sign("s3", body) == f"sha256={expected}"

	def test_validate_rejects_private_and_bad_scheme(self, dns):
		assert validate_webhook_url(" https://hooks.example.com/x ") == "https://hooks.example.com/x"
		with self.assertRaises(WebhookUrlError):
			validate_webhook_url("ftp://hooks.example.com/x")
		dns.return_value = PRIVATE
		with self.assertRaises(WebhookUrlError):
			validate_webhook_url("https://internal.example.com/x")

	@mock.patch("channels_app.webhooks.deliver_webhook.delay")
	def test_emit_only_when_active_with_url(self, delay, _dns):
		emit_event(self.project, "conversation.handoff", {"x": 1})
		assert delay.call_count == 1
		hook_id, payload = delay.call_args.args
		assert hook_id == str(self.hook.id)
		assert payload["event"] == "conversation.handoff" and payload["data"] == {"x": 1}
		assert payload["project_id"] == str(self.project.id) and payload["id"]

		self.hook.is_active = False
		self.hook.save()
		emit_event(self.project, "conversation.handoff", {})
		assert delay.call_count == 1

	@mock.patch("channels_app.webhooks.requests.post")
	def test_delivery_signs_body_and_headers(self, post, _dns):
		post.return_value = mock.Mock(status_code=200)
		payload = build_event(self.project.id, "ping", {})
		assert deliver_webhook.apply(args=[str(self.hook.id), payload]).get() == 200
		kwargs = post.call_args.kwargs
		assert kwargs["allow_redirects"] is False and kwargs["timeout"] == 10
		assert json.loads(kwargs["data"]) == payload
		h = kwargs["headers"]
		assert h["X-AMP-Event"] == "ping" and h["X-AMP-Delivery"] == payload["id"]
		assert h["X-AMP-Signature"] == sign(self.hook.secret, kwargs["data"])

	@mock.patch("channels_app.webhooks.requests.post")
	def test_retries_on_5xx(self, post, _dns):
		post.return_value = mock.Mock(status_code=503)
		deliver_webhook.apply(args=[str(self.hook.id), build_event(self.project.id, "ping", {})])
		assert post.call_count == 4  # first try + 3 retries

	@mock.patch("channels_app.webhooks.requests.post")
	def test_no_retry_on_4xx(self, post, _dns):
		post.return_value = mock.Mock(status_code=404)
		deliver_webhook.apply(args=[str(self.hook.id), build_event(self.project.id, "ping", {})])
		assert post.call_count == 1

	@mock.patch("channels_app.webhooks.requests.post")
	def test_delivery_skipped_when_url_turns_private(self, post, dns):
		dns.return_value = PRIVATE
		result = deliver_webhook.apply(args=[str(self.hook.id), build_event(self.project.id, "ping", {})])
		assert result.get() is None
		post.assert_not_called()
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_webhooks -v 2`
Expected: ERROR `No module named 'channels_app.webhooks'`

- [ ] **Step 3: Implement** — `backend/channels_app/webhooks.py`:

```python
"""Signed outbound webhooks for project events (conversation.handoff, conversation.resumed, message.staff_reply)."""
import hashlib
import hmac
import json
import logging
import uuid
from typing import Optional

import requests
from celery import shared_task
from django.utils import timezone

from agents.api_tool import _is_public_host, _parse_target
from .models import ProjectWebhook

logger = logging.getLogger(__name__)

TIMEOUT = 10
RETRY_COUNTDOWNS = [10, 60, 300]


class WebhookUrlError(ValueError):
	pass


def validate_webhook_url(url: str) -> str:
	"""Same rules as the agent's call_api tool: http(s), public addresses only."""
	url = (url or "").strip()
	try:
		scheme, host = _parse_target(url)
	except ValueError as e:
		raise WebhookUrlError(str(e))
	if scheme not in ("http", "https") or not host:
		raise WebhookUrlError("URL must start with http:// or https://")
	if not _is_public_host(host):
		raise WebhookUrlError(f"{host} is not a public address")
	return url


def sign(secret: str, body: bytes) -> str:
	return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_event(project_id, event: str, data: dict) -> dict:
	return {
		"id": str(uuid.uuid4()),
		"event": event,
		"created_at": timezone.now().isoformat(),
		"project_id": str(project_id),
		"data": data,
	}


def post_event(hook: ProjectWebhook, payload: dict) -> int:
	"""POST one signed event; returns the HTTP status. Raises WebhookUrlError or requests.RequestException."""
	validate_webhook_url(hook.url)  # re-checked per delivery: DNS may have changed since save
	body = json.dumps(payload, separators=(",", ":")).encode()
	resp = requests.post(
		hook.url,
		data=body,
		timeout=TIMEOUT,
		allow_redirects=False,
		headers={
			"Content-Type": "application/json",
			"X-AMP-Event": payload["event"],
			"X-AMP-Delivery": payload["id"],
			"X-AMP-Signature": sign(hook.secret, body),
		},
	)
	return resp.status_code


def emit_event(project, event: str, data: dict) -> None:
	hook = ProjectWebhook.objects.filter(project=project, is_active=True).exclude(url="").first()
	if hook:
		deliver_webhook.delay(str(hook.id), build_event(project.id, event, data))


@shared_task(bind=True, max_retries=len(RETRY_COUNTDOWNS))
def deliver_webhook(self, hook_id: str, payload: dict) -> Optional[int]:
	hook = ProjectWebhook.objects.filter(id=hook_id).first()
	if not hook or not hook.is_active or not hook.url:
		return None
	try:
		status = post_event(hook, payload)
	except WebhookUrlError as e:
		logger.warning(f"Webhook {payload['event']} {payload['id']} skipped: {e}")
		return None
	except requests.RequestException as e:
		logger.warning(f"Webhook {payload['event']} {payload['id']} failed: {type(e).__name__}")
		status = None
	if status is not None and status < 500:
		if status >= 400:
			logger.warning(f"Webhook {payload['event']} {payload['id']} rejected with HTTP {status}")
		return status
	if self.request.retries < self.max_retries:
		raise self.retry(countdown=RETRY_COUNTDOWNS[self.request.retries])
	logger.error(f"Webhook {payload['event']} {payload['id']} failed after retries (last status {status})")
	return status
```

- [ ] **Step 4: Run, verify pass**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_webhooks -v 2`
Expected: 7 tests OK.

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/webhooks.py backend/channels_app/tests/test_webhooks.py
git commit -m "feat(channels): signed outbound webhooks with retries and SSRF guard"
```

---

### Task 3: Handoff actions, agent tool, prompt

**Files:**
- Modify: `backend/channels_app/handoff.py` (append actions)
- Modify: `backend/agents/rag.py` (`Context.handoff_reason`; `escalate_to_human` tool in `_build_tools_from_context`)
- Modify: `backend/agents/llm.py` (`build_system_prompt` HANDOFF section)
- Test: `backend/channels_app/tests/test_handoff_actions.py`, `backend/agents/tests/test_handoff_tool.py`

**Interfaces:**
- Consumes: Task 1 fields; `emit_event` (Task 2).
- Produces:
  - `conversation_event_data(conversation) -> dict`
  - `start_handoff(conversation, reason: str) -> None` (emits `conversation.handoff`)
  - `end_handoff(conversation) -> None` (emits `conversation.resumed`)
  - `note_staff_activity(conversation) -> None` (pauses with reason "staff took over" if active, else refreshes `handoff_at`)
  - `Context.handoff_reason: Optional[str]`; agent tool named `escalate_to_human(reason: str) -> str`

- [ ] **Step 1: Write failing tests**

`backend/channels_app/tests/test_handoff_actions.py`:

```python
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from accounts.models import Project
from channels_app.handoff import end_handoff, note_staff_activity, start_handoff
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase


@mock.patch("channels_app.handoff.emit_event")
class HandoffActionsTest(TestCase):
	def setUp(self):
		project = Project.objects.create(name="P", slug="p-handoff")
		kb = KnowledgeBase.objects.create(project=project, name="kb", vector_collection="vc-handoff")
		contact = CustomerContact.objects.create(project=project, platform="facebook", external_id="psid", display_name="Ann")
		self.conv = Conversation.objects.create(project=project, contact=contact, knowledge_base=kb)

	def test_start_and_end_emit_events(self, emit):
		start_handoff(self.conv, "refund request")
		self.conv.refresh_from_db()
		assert self.conv.is_paused() and self.conv.handoff_reason == "refund request"
		event, data = emit.call_args.args[1:]
		assert event == "conversation.handoff"
		assert data["reason"] == "refund request" and data["channel"] == "facebook"
		assert data["contact"]["display_name"] == "Ann"

		end_handoff(self.conv)
		self.conv.refresh_from_db()
		assert not self.conv.is_paused() and self.conv.handoff_reason == ""
		assert emit.call_args.args[1] == "conversation.resumed"

	def test_staff_activity_pauses_active_conversation(self, emit):
		note_staff_activity(self.conv)
		self.conv.refresh_from_db()
		assert self.conv.is_paused() and self.conv.handoff_reason == "staff took over"
		assert emit.call_args.args[1] == "conversation.handoff"

	def test_staff_activity_refreshes_paused_conversation(self, emit):
		self.conv.handoff_at = timezone.now() - timedelta(hours=20)
		self.conv.handoff_reason = "angry customer"
		self.conv.save()
		note_staff_activity(self.conv)
		self.conv.refresh_from_db()
		assert timezone.now() - self.conv.handoff_at < timedelta(minutes=1)
		assert self.conv.handoff_reason == "angry customer"
		emit.assert_not_called()
```

`backend/agents/tests/test_handoff_tool.py`:

```python
from django.test import SimpleTestCase

from agents.llm import build_system_prompt
from agents.rag import Context, ToolDeps, _build_tools_from_context


class HandoffToolTest(SimpleTestCase):
	def test_tool_always_present_and_records_reason(self):
		ctx = Context()
		tools = {t.__name__: t for t in _build_tools_from_context(ctx, ToolDeps())}
		assert "escalate_to_human" in tools
		assert ctx.handoff_reason is None
		out = tools["escalate_to_human"]("customer wants a refund")
		assert ctx.handoff_reason == "customer wants a refund"
		assert "person will follow up" in out

	def test_prompt_mentions_handoff(self):
		prompt = build_system_prompt()
		assert "escalate_to_human" in prompt
		assert "refund" in prompt
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_handoff_actions -v 2` → Expected: ImportError `cannot import name 'end_handoff'`.
Run: `docker compose run --rm django python manage.py test agents.tests.test_handoff_tool -v 2` → Expected: FAIL `'escalate_to_human' not in ...`.

- [ ] **Step 3: Handoff actions** — append to `backend/channels_app/handoff.py` (and add imports at top: `from django.utils import timezone` and `from .webhooks import emit_event`):

```python
def conversation_event_data(conversation) -> dict:
	contact = conversation.contact
	return {
		"conversation_id": str(conversation.id),
		"channel": conversation.channel,
		"reason": conversation.handoff_reason,
		"contact": {
			"id": str(contact.id),
			"platform": contact.platform,
			"external_id": contact.external_id,
			"display_name": contact.display_name,
		} if contact else None,
	}


def start_handoff(conversation, reason: str) -> None:
	conversation.handoff_at = timezone.now()
	conversation.handoff_reason = (reason or "needs a person").strip()[:255]
	conversation.save(update_fields=["handoff_at", "handoff_reason", "updated_at"])
	emit_event(conversation.project, "conversation.handoff", conversation_event_data(conversation))


def end_handoff(conversation) -> None:
	conversation.handoff_at = None
	conversation.handoff_reason = ""
	conversation.save(update_fields=["handoff_at", "handoff_reason", "updated_at"])
	emit_event(conversation.project, "conversation.resumed", conversation_event_data(conversation))


def note_staff_activity(conversation) -> None:
	"""A staff reply pauses the bot, or restarts the auto-resume clock if already paused."""
	if conversation.is_paused():
		conversation.handoff_at = timezone.now()
		conversation.save(update_fields=["handoff_at", "updated_at"])
	else:
		start_handoff(conversation, "staff took over")
```

- [ ] **Step 4: Agent tool** — in `backend/agents/rag.py`:

Add to `Context` after `api_call_log`:

```python
	handoff_reason: Optional[str] = None  # set by escalate_to_human during the reply
```

In `_build_tools_from_context`, immediately before the `if context.api_domains:` block:

```python
	def escalate_to_human(reason: str) -> str:
		"""Hand this conversation to a human staff member. Use it when the customer asks for a person,
		is angry or abusive, raises a refund, complaint, cancellation or legal matter, when you could not
		answer their question twice in a row, or when the custom instructions say so. Give a short reason."""
		context.handoff_reason = (reason or "").strip() or "customer needs a person"
		logger.info(f"[Tool: escalate_to_human] reason={context.handoff_reason!r}")
		return "Handed off. Tell the customer a person will follow up."

	tools.append(escalate_to_human)
```

- [ ] **Step 5: Prompt** — in `backend/agents/llm.py` `build_system_prompt`, append to `base_prompt` (after the last RESPONSE RULES line, inside the parentheses):

```python
		"\nHANDING OFF TO A PERSON:\n"
		"- Call escalate_to_human when the customer asks for a person, is angry or abusive, raises a refund, complaint, cancellation or legal matter, or when you could not answer their question twice in a row\n"
		"- The custom instructions below may add or change these triggers; follow them\n"
		"- After calling it, reply only with a short note that a person from the team will follow up\n"
```

- [ ] **Step 6: Run, verify pass**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_handoff_actions -v 2` → Expected: 3 OK.
Run: `docker compose run --rm django python manage.py test agents -v 2` → Expected: all agents tests OK (24).

- [ ] **Step 7: Commit**

```bash
git add backend/channels_app/handoff.py backend/agents/rag.py backend/agents/llm.py backend/channels_app/tests/test_handoff_actions.py backend/agents/tests/test_handoff_tool.py
git commit -m "feat(handoff): pause/resume actions, escalate_to_human tool, prompt triggers"
```

---

### Task 4: Shared reply pipeline

**Files:**
- Create: `backend/channels_app/reply.py`
- Modify: `backend/channels_app/tasks.py` (`build_history` legacy filter; `generate_agent_reply` signature/return; wrappers)
- Modify: `backend/channels_app/service/conversation_service.py` (add `ensure_contact_conversation`)
- Delete: `backend/channels_app/tests/test_reply_retry.py` (ported)
- Test: `backend/channels_app/tests/test_reply_pipeline.py`

**Interfaces:**
- Consumes: Task 1 (`is_paused`, `channel`, `sender`), Task 3 (`wants_human`, `start_handoff`, `Context.handoff_reason`).
- Produces:
  - `channels_app.reply.schedule_reply(msg: Message) -> None`
  - Celery task `channels_app.reply.process_reply(conversation_id: str, message_id: str) -> None`
  - `channels_app.reply.pending_turn(conversation) -> list[Message]`
  - `channels_app.reply.deliver(conversation, out_msg, reference_msg=None) -> bool`
  - `tasks.generate_agent_reply(msg, knowledge_base, question: Optional[str] = None) -> tuple[str, Context]`
  - `conversation_service.ensure_contact_conversation(contact, knowledge_base, name: str = "") -> Conversation`

- [ ] **Step 1: Write failing tests** — `backend/channels_app/tests/test_reply_pipeline.py`:

```python
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from accounts.models import Project
from channels_app.models import CustomerContact, Message
from channels_app.reply import process_reply
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from channels_app.service.message_service import create_ui_message
from channels_app.tasks import FALLBACK_REPLY, handle_incoming_message, handle_ui_message
from knowledge.models import KnowledgeBase


class PipelineBase(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-pipe")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-pipe")
		self.contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.questions, self.histories = [], []
		self.reply_text = "Here you go"

		def fake_generate_reply(question, context, deps=None, history=None, project_id=None):
			self.questions.append(question)
			self.histories.append([h["content"] for h in history or []])
			return self.reply_text

		self.gen = mock.patch("channels_app.tasks.generate_reply", side_effect=fake_generate_reply).start()
		self.send = mock.patch("channels_app.tasks.send_text_message", return_value={"message_id": "mid.out"}).start()
		cred = mock.Mock()
		cred.get_credentials.return_value.page_access_token = "tok"
		mock.patch("channels_app.tasks.get_facebook_credential", return_value=cred).start()
		self.emit = mock.patch("channels_app.handoff.emit_event").start()
		self.addCleanup(mock.patch.stopall)

	def inbound(self, text, **extra):
		return Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text=text, **extra
		)

	def run_reply(self, msg):
		process_reply.apply(args=[str(msg.conversation_id), str(msg.id)])

	def outbound(self):
		return list(Message.objects.filter(conversation=self.conv, direction="out").order_by("created_at"))


class BatchingTest(PipelineBase):
	def test_older_message_task_skips_when_newer_exists(self):
		a = self.inbound("hi")
		self.inbound("price of X?")
		self.run_reply(a)
		assert self.gen.call_count == 0
		a.refresh_from_db()
		assert a.status == "completed"
		assert self.outbound() == []

	def test_newest_task_answers_whole_burst_once(self):
		a = self.inbound("hi")
		b = self.inbound("price of X?")
		self.run_reply(b)
		assert self.gen.call_count == 1
		assert "hi" in self.questions[0] and "price of X?" in self.questions[0]
		out = self.outbound()
		assert len(out) == 1 and out[0].text == "Here you go" and out[0].sender == "bot"
		assert {m.status for m in Message.objects.filter(id__in=[a.id, b.id])} == {"completed"}
		self.send.assert_called_once()
		out[0].refresh_from_db()
		assert out[0].external_message_id == "mid.out"


class HandoffPipelineTest(PipelineBase):
	def test_paused_conversation_gets_no_reply(self):
		self.conv.handoff_at = timezone.now()
		self.conv.save()
		m = self.inbound("anyone?")
		self.run_reply(m)
		assert self.gen.call_count == 0 and self.outbound() == []
		self.send.assert_not_called()
		m.refresh_from_db()
		assert m.status == "completed"

	def test_messages_during_pause_are_answered_after_resume(self):
		self.conv.handoff_at = timezone.now() - timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS + 1)
		self.conv.save()  # expired pause
		self.inbound("first while paused", status="completed")
		m = self.inbound("still there?")
		self.run_reply(m)
		assert "first while paused" in self.questions[0] and "still there?" in self.questions[0]

	def test_keyword_rule_hands_off_without_llm(self):
		m = self.inbound("can I talk to a person")
		self.run_reply(m)
		assert self.gen.call_count == 0
		self.conv.refresh_from_db()
		assert self.conv.is_paused() and self.conv.handoff_reason == "customer asked for a person"
		assert self.outbound()[0].text == settings.HANDOFF_MESSAGE
		assert self.emit.call_args.args[1] == "conversation.handoff"
		self.send.assert_called_once()

	def test_agent_tool_handoff_replaces_reply(self):
		def tool_handoff(question, context, *args, **kwargs):
			context.handoff_reason = "refund request"
			return "Sure, I'll process your refund"

		self.gen.side_effect = tool_handoff
		self.run_reply(self.inbound("I want my money back"))
		self.conv.refresh_from_db()
		assert self.conv.handoff_reason == "refund request"
		assert self.outbound()[0].text == settings.HANDOFF_MESSAGE


class HistoryAndRetryTest(PipelineBase):
	def test_history_includes_legacy_messages_without_conversation(self):
		Message.objects.create(project=self.project, contact=self.contact, conversation=None, direction="in", text="old question")
		self.run_reply(self.inbound("new question"))
		assert "old question" in self.histories[0]

	def test_failed_reply_after_post_is_not_retried_and_calls_are_logged(self):
		calls = [{"method": "POST", "url": "https://shop.com/book", "status": 201, "ms": 5}]

		def post_then_fail(question, context, *args, **kwargs):
			context.api_call_log.extend(calls)
			raise RuntimeError("llm down")

		self.gen.side_effect = post_then_fail
		self.run_reply(self.inbound("book it"))
		assert self.gen.call_count == 1
		out = self.outbound()[0]
		assert out.text == FALLBACK_REPLY and out.meta["api_calls"] == calls


class SelfChannelTest(PipelineBase):
	def test_ui_message_reply_uses_agent_reply_id_and_no_send(self):
		conv = create_conversation(project=self.project, knowledge_base=self.kb, user_id="u1")
		msg = create_ui_message(conversation=conv, text="hello")
		process_reply.apply(args=[str(conv.id), str(msg.id)])
		out = Message.objects.get(conversation=conv, direction="out")
		assert out.id == msg.agent_reply_id
		self.send.assert_not_called()


class LegacyWrapperTest(PipelineBase):
	@mock.patch("channels_app.reply.process_reply.delay")
	def test_legacy_incoming_wrapper_creates_conversation(self, delay):
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid2")
		msg = Message.objects.create(project=self.project, contact=contact, direction="in", text="hi")
		handle_incoming_message.apply(args=[str(msg.id), str(self.kb.id)])
		msg.refresh_from_db()
		assert msg.conversation_id is not None
		delay.assert_called_once_with(str(msg.conversation_id), str(msg.id))

	@mock.patch("channels_app.reply.process_reply.delay")
	def test_legacy_ui_wrapper_enqueues(self, delay):
		m = self.inbound("hi")
		handle_ui_message.apply(args=[str(m.id)])
		delay.assert_called_once_with(str(self.conv.id), str(m.id))
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_reply_pipeline -v 2`
Expected: ERROR `No module named 'channels_app.reply'`

- [ ] **Step 3: `ensure_contact_conversation`** — append to `backend/channels_app/service/conversation_service.py` (add `from django.db import transaction` to imports):

```python
def ensure_contact_conversation(contact: CustomerContact, knowledge_base: KnowledgeBase, name: str = "") -> Conversation:
	"""The single conversation of an external-platform contact, created on first use."""
	with transaction.atomic():
		contact = CustomerContact.objects.select_for_update().get(pk=contact.pk)  # two webhooks at once must not create two
		if contact.conversation_id:
			return contact.conversation
		return create_conversation(project=contact.project, knowledge_base=knowledge_base, name=name, contact=contact)
```

- [ ] **Step 4: `tasks.py` changes**

Add `from django.db.models import Q` to the imports.

Replace the body of `build_history` with:

```python
	"""Last HISTORY_LIMIT messages before `msg`, oldest first.
	Includes the contact's older messages saved before Facebook chats had conversations."""
	if msg.conversation_id:
		qs = Message.objects.filter(
			Q(conversation_id=msg.conversation_id) | Q(contact_id=msg.contact_id, conversation__isnull=True)
		)
	else:
		qs = Message.objects.filter(contact=msg.contact)
	recent = list(
		qs.exclude(id=msg.id)
		.filter(created_at__lte=msg.created_at)
		.select_related("reply_to_message")
		.order_by("-created_at")[:HISTORY_LIMIT]
	)
	recent.reverse()
	return [
		ChatMessage(role=("user" if m.direction == "in" else "assistant"), content=format_message_with_context(m))
		for m in recent
	]
```

Change `generate_agent_reply`:
- signature → `def generate_agent_reply(msg: Message, knowledge_base: KnowledgeBase, question: Optional[str] = None) -> tuple[str, Context]:` (add `from typing import Optional` to imports)
- docstring → `"""Run the RAG agent with history before `msg`. `question` defaults to `msg` formatted. Returns (reply text, Context with api_call_log/handoff_reason). DB reads only; call outside any transaction."""`
- `current_message_text = question or format_message_with_context(msg)`
- last line → `return reply_text, context`

Replace the entire `handle_incoming_message` and `handle_ui_message` tasks (from `@shared_task(bind=True, max_retries=2, default_retry_delay=5)` to end of file) with:

```python
@shared_task
def handle_incoming_message(message_id: str, knowledge_base_id: str) -> None:
	"""Legacy entry point for messages queued before channels_app.reply existed."""
	from .reply import process_reply
	from .service.conversation_service import ensure_contact_conversation

	msg = Message.objects.select_related("contact", "project").get(id=message_id)
	if not msg.conversation_id:
		msg.conversation = ensure_contact_conversation(msg.contact, KnowledgeBase.objects.get(id=knowledge_base_id))
		msg.save(update_fields=["conversation"])
	process_reply.delay(str(msg.conversation_id), str(msg.id))


@shared_task
def handle_ui_message(message_id: str) -> None:
	"""Legacy entry point for messages queued before channels_app.reply existed."""
	from .reply import process_reply

	msg = Message.objects.get(id=message_id)
	process_reply.delay(str(msg.conversation_id), str(msg.id))
```

Remove now-unused imports from `tasks.py` only if nothing else uses them: keep `send_text_message`, `ChannelCredential`, `create_outbound_message` imports (reply.py uses `tasks.send_text_message` for patchability); remove `from django.db import transaction` and `import uuid` if unused.

- [ ] **Step 5: Implement** — `backend/channels_app/reply.py`:

```python
"""
Channel-agnostic reply pipeline: batching, handoff, agent reply, delivery.

Channels call schedule_reply(msg) for every inbound customer message. Helpers live in
channels_app.tasks and are referenced as `tasks.X` so tests can patch them in one place.
"""
import logging
from typing import Optional

from celery import shared_task
from django.conf import settings
from django.db import transaction

from . import tasks
from .handoff import start_handoff, wants_human
from .models import Conversation, Message
from .service.message_service import create_outbound_message

logger = logging.getLogger(__name__)


def schedule_reply(msg: Message) -> None:
	delay = settings.REPLY_DEBOUNCE_SECONDS.get(msg.conversation.channel, 0)
	process_reply.apply_async(args=[str(msg.conversation_id), str(msg.id)], countdown=delay)


def pending_turn(conversation) -> list:
	"""Inbound messages since the last outbound message (bot or staff), oldest first."""
	last_out = (
		Message.objects.filter(conversation=conversation, direction="out")
		.order_by("-created_at").values_list("created_at", flat=True).first()
	)
	qs = Message.objects.filter(conversation=conversation, direction="in").select_related("reply_to_message")
	if last_out:
		qs = qs.filter(created_at__gt=last_out)
	return list(qs.order_by("created_at"))


def _complete(messages) -> None:
	Message.objects.filter(id__in=[m.id for m in messages]).update(status="completed")


def _send_facebook(conversation, out_msg: Message, reference_msg: Message) -> bool:
	cred = tasks.get_facebook_credential(reference_msg)
	response = tasks.send_text_message(
		page_access_token=cred.get_credentials().page_access_token,
		recipient_psid=conversation.contact.external_id,
		text=out_msg.text,
	) if cred else {}
	if response.get("message_id"):
		out_msg.external_message_id = response["message_id"]
		out_msg.meta = {**out_msg.meta, **response}
		out_msg.save(update_fields=["external_message_id", "meta"])
		return True
	logger.warning(f"Facebook send failed for message {out_msg.id} (credential found: {bool(cred)})")
	out_msg.meta = {**out_msg.meta, "send_error": "facebook send failed"}
	out_msg.save(update_fields=["meta"])
	return False


SENDERS = {
	"facebook": _send_facebook,
	"self": lambda conversation, out_msg, reference_msg: True,  # stored row is the delivery (dashboard, future widget/API)
}


def deliver(conversation, out_msg: Message, reference_msg: Optional[Message] = None) -> bool:
	sender = SENDERS.get(conversation.channel)
	if not sender:
		logger.error(f"No sender for channel {conversation.channel!r}; message {out_msg.id} not delivered")
		return False
	return sender(conversation, out_msg, reference_msg or out_msg)


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def process_reply(self, conversation_id: str, message_id: str) -> None:
	conversation = Conversation.objects.select_related("project", "contact", "knowledge_base").get(id=conversation_id)
	msg = Message.objects.get(id=message_id)

	# Batching: a newer message's task will answer the whole burst
	# ponytail: if this task is mid-LLM when a newer message lands, both may answer the older one; add a per-conversation lock if that shows up
	if Message.objects.filter(conversation=conversation, direction="in", created_at__gt=msg.created_at).exists():
		_complete([msg])
		return

	turn = pending_turn(conversation)
	if not turn:
		return
	if conversation.is_paused():
		_complete(turn)  # staff answer these
		return

	question = "\n".join(tasks.format_message_with_context(m) for m in turn)
	api_calls: list = []
	handoff_reason = None
	if wants_human(question):
		reply_text = ""
		handoff_reason = "customer asked for a person"
	else:
		try:
			reply_text, ctx = tasks.generate_agent_reply(turn[0], conversation.knowledge_base, question=question)
			api_calls, handoff_reason = ctx.api_call_log, ctx.handoff_reason
		except Exception as exc:
			api_calls = getattr(exc, "api_calls", [])
			# A retry re-runs the whole agent; never risk sending a POST (order, booking, ticket) twice
			sent_post = any(c.get("method") == "POST" for c in api_calls)
			if not sent_post and self.request.retries < self.max_retries:
				logger.warning(f"Reply generation failed for conversation {conversation.id}, retrying: {exc}")
				raise self.retry(exc=exc)
			logger.error(f"Reply generation failed for conversation {conversation.id}: {exc}", exc_info=True)
			reply_text = ""

	if handoff_reason:
		start_handoff(conversation, handoff_reason)
		reply_text = settings.HANDOFF_MESSAGE
	reply_text = reply_text or tasks.FALLBACK_REPLY

	newest = turn[-1]
	with transaction.atomic():
		out_msg = create_outbound_message(
			project=conversation.project,
			contact=conversation.contact,
			text=reply_text,
			meta={"api_calls": api_calls} if api_calls else {},
			conversation=conversation,
			id=newest.agent_reply_id or None,
		)
		_complete(turn)
	deliver(conversation, out_msg, newest)
```

- [ ] **Step 6: Remove the ported test**

```bash
git rm backend/channels_app/tests/test_reply_retry.py
```

- [ ] **Step 7: Run, verify pass**

Run: `docker compose run --rm django python manage.py test channels_app -v 2`
Expected: all new pipeline tests (12) OK plus `test_reply_history`, `test_handoff_rules`, `test_handoff_actions`, `test_webhooks` OK; only the known pre-existing `test_facebook_webhook.test_get_verification` fails.

- [ ] **Step 8: Commit**

```bash
git add backend/channels_app/reply.py backend/channels_app/tasks.py backend/channels_app/service/conversation_service.py backend/channels_app/tests/test_reply_pipeline.py
git commit -m "feat(reply): shared pipeline with batching, handoff and per-channel senders"
```

---

### Task 5: Facebook webhook creates conversations and schedules replies

**Files:**
- Modify: `backend/channels_app/facebook_client.py` (add `get_user_name`)
- Modify: `backend/channels_app/service/facebook_messing.py`
- Test: `backend/channels_app/tests/test_facebook_conversation.py`

**Interfaces:**
- Consumes: `ensure_contact_conversation` (Task 4), `schedule_reply` (Task 4), `CustomerContact.display_name` (Task 1).
- Produces: `facebook_client.get_user_name(page_access_token: str, psid: str) -> str` ("" on failure).

- [ ] **Step 1: Write failing tests** — `backend/channels_app/tests/test_facebook_conversation.py`:

```python
from unittest import mock

from django.test import TestCase

from accounts.models import Project
from channels_app.models import ChannelCredential, CustomerContact, Message
from channels_app.service.facebook_messing import handle_facebook_message
from knowledge.models import KnowledgeBase


def payload(mid, text="hello"):
	return {"entry": [{"messaging": [{
		"sender": {"id": "psid-1"}, "recipient": {"id": "page-1"}, "timestamp": 1,
		"message": {"mid": mid, "text": text},
	}]}]}


@mock.patch("channels_app.service.facebook_messing.schedule_reply")
class FacebookConversationTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-fb")
		kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-fb")
		ChannelCredential.objects.create(
			project=self.project, channel="facebook", knowledge_base=kb, is_active=True,
			credentials={"page_id": "page-1", "page_access_token": "tok"},
		)

	@mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="Ann Lee")
	def test_first_message_creates_named_conversation_and_schedules(self, get_name, schedule):
		handle_facebook_message(payload("m1"))
		contact = CustomerContact.objects.get(external_id="psid-1")
		assert contact.display_name == "Ann Lee"
		assert contact.conversation is not None and contact.conversation.name == "Ann Lee"
		msg = Message.objects.get(external_message_id="m1")
		assert msg.conversation_id == contact.conversation_id and msg.sender == "customer"
		schedule.assert_called_once()
		assert schedule.call_args.args[0].id == msg.id
		get_name.assert_called_once_with("tok", "psid-1")

		handle_facebook_message(payload("m2"))
		assert Message.objects.get(external_message_id="m2").conversation_id == contact.conversation_id
		get_name.assert_called_once()  # not looked up again

	@mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="")
	def test_name_lookup_failure_is_tolerated(self, _get_name, schedule):
		handle_facebook_message(payload("m1"))
		contact = CustomerContact.objects.get(external_id="psid-1")
		assert contact.display_name == "" and contact.conversation.name == "Facebook user"
		schedule.assert_called_once()
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_facebook_conversation -v 2`
Expected: ERROR `AttributeError: ... does not have the attribute 'schedule_reply'`.

- [ ] **Step 3: `get_user_name`** — append to `backend/channels_app/facebook_client.py`:

```python
GRAPH_API_URL = "https://graph.facebook.com/v21.0"


def get_user_name(page_access_token: str, psid: str) -> str:
	"""Customer's name for the dashboard (so staff can find them in the Meta inbox). "" on any failure."""
	try:
		resp = requests.get(
			f"{GRAPH_API_URL}/{psid}",
			params={"fields": "name", "access_token": page_access_token},
			timeout=5,
		)
		if resp.status_code == 200:
			return (resp.json().get("name") or "")[:255]
		logger.warning(f"Facebook name lookup failed: HTTP {resp.status_code}")
	except (requests.RequestException, ValueError) as e:
		logger.warning(f"Facebook name lookup failed: {type(e).__name__}")
	return ""
```

- [ ] **Step 4: Webhook handler** — in `backend/channels_app/service/facebook_messing.py`:

Replace the import `from channels_app.tasks import handle_incoming_message` with:

```python
from channels_app.facebook_client import get_user_name
from channels_app.reply import schedule_reply
from channels_app.service.conversation_service import ensure_contact_conversation
```

After the `contact, _ = CustomerContact.objects.get_or_create(...)` statement add:

```python
			conversation = contact.conversation
			if conversation is None:
				if not contact.display_name:
					contact.display_name = get_user_name(cred.get_credentials().page_access_token, sender_id)
					if contact.display_name:
						contact.save(update_fields=["display_name"])
				conversation = ensure_contact_conversation(
					contact, knowledge_base, name=contact.display_name or "Facebook user"
				)
```

Pass `conversation=conversation,` to the `create_inbound_message(...)` call, and replace

```python
				handle_incoming_message.delay(str(msg.id), str(knowledge_base.id))
```

with

```python
				schedule_reply(msg)
```

- [ ] **Step 5: Run, verify pass**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_facebook_conversation -v 2`
Expected: 2 OK.

- [ ] **Step 6: Commit**

```bash
git add backend/channels_app/facebook_client.py backend/channels_app/service/facebook_messing.py backend/channels_app/tests/test_facebook_conversation.py
git commit -m "feat(facebook): conversation per contact with display name; replies via shared pipeline"
```

---

### Task 6: Conversation, staff-reply and webhook API

**Files:**
- Modify: `backend/channels_app/api.py`
- Modify: `backend/channels_app/urls_api.py`
- Test: `backend/channels_app/tests/test_handoff_api.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces (all under `/api/channels/`):
  - Conversation JSON (list, create, detail, patch): `{id, name, knowledge_base_id, knowledge_base_name, channel, contact_display_name, handoff_at, handoff_reason, is_paused, resumes_at, created_at, updated_at}`; list items also `message_count`, `last_message`; list response also `needs_human_count`; `GET conversations?project_slug=..&needs_human=1` filters to paused.
  - Message JSON gains `sender`.
  - `POST conversations/<id>/handoff` `{reason?}` → `{conversation}`
  - `POST conversations/<id>/resume` → `{conversation}`
  - `POST conversations/<id>/staff-reply` `{text}` → 201 `{message}`; 400 `invalid_text` / `outside_messaging_window`; 502 `send_failed` (message saved)
  - `POST conversations/<id>/messages` → 400 `not_a_test_conversation` for non-`self`
  - `GET webhook?project_slug=` → `{webhook: {url, is_active, secret}}`; `PUT webhook` `{project_slug, url, is_active}` → same, 400 `invalid_url`; `POST webhook/rotate-secret` `{project_slug}`; `POST webhook/test` `{project_slug}` → `{status}` or 400 `invalid_url` / 502 `delivery_failed`

- [ ] **Step 1: Write failing tests** — `backend/channels_app/tests/test_handoff_api.py`:

```python
import socket
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import CustomerContact, Message, ProjectWebhook
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from knowledge.models import KnowledgeBase

BASE = "/api/channels"
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]


class HandoffApiTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="a@x.com", email="a@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-api")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(self.user)
		kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-api")
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid", display_name="Ann")
		self.fb = ensure_contact_conversation(contact, kb, name="Ann")
		self.ui = create_conversation(project=self.project, knowledge_base=kb, user_id=str(self.user.id))
		self.emit = mock.patch("channels_app.handoff.emit_event").start()
		self.api_emit = mock.patch("channels_app.api.emit_event").start()
		self.send = mock.patch("channels_app.tasks.send_text_message", return_value={"message_id": "mid"}).start()
		cred = mock.Mock()
		cred.get_credentials.return_value.page_access_token = "tok"
		mock.patch("channels_app.tasks.get_facebook_credential", return_value=cred).start()
		self.addCleanup(mock.patch.stopall)

	def customer_says(self, text="hi", hours_ago=0):
		m = Message.objects.create(project=self.project, contact=self.fb.contact, conversation=self.fb, direction="in", text=text)
		if hours_ago:
			Message.objects.filter(id=m.id).update(created_at=timezone.now() - timedelta(hours=hours_ago))
		return m

	def test_handoff_and_resume(self):
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/handoff", {"reason": "VIP"}, format="json")
		assert r.status_code == 200, r.content
		conv = r.json()["data"]["conversation"]
		assert conv["is_paused"] and conv["handoff_reason"] == "VIP" and conv["resumes_at"]
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/resume", format="json")
		assert r.json()["data"]["conversation"]["is_paused"] is False
		assert [c.args[1] for c in self.emit.call_args_list] == ["conversation.handoff", "conversation.resumed"]

	def test_non_member_rejected(self):
		other = get_user_model().objects.create_user(username="b@x.com", email="b@x.com", password="pw123456")
		self.client.force_authenticate(other)
		assert self.client.post(f"{BASE}/conversations/{self.fb.id}/handoff", format="json").status_code == 400

	def test_staff_reply_facebook_sends_and_pauses(self):
		self.customer_says()
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "Hi Ann, Sam here"}, format="json")
		assert r.status_code == 201, r.content
		assert r.json()["data"]["message"]["sender"] == "staff"
		self.send.assert_called_once()
		self.fb.refresh_from_db()
		assert self.fb.is_paused() and self.fb.handoff_reason == "staff took over"
		assert self.api_emit.call_args.args[1] == "message.staff_reply"

	def test_staff_reply_facebook_outside_window_rejected(self):
		self.customer_says(hours_ago=25)
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "late"}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "outside_messaging_window"
		assert not Message.objects.filter(sender="staff").exists()

	def test_staff_reply_facebook_without_inbound_is_rejected(self):
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "hello?"}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "outside_messaging_window"

	def test_staff_reply_send_failure_returns_502_and_keeps_message(self):
		self.customer_says()
		self.send.return_value = {}
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "Hi"}, format="json")
		assert r.status_code == 502 and r.json()["error"]["code"] == "send_failed"
		assert Message.objects.get(sender="staff").meta["send_error"]

	def test_staff_reply_text_validated(self):
		r = self.client.post(f"{BASE}/conversations/{self.ui.id}/staff-reply", {"text": "  "}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_text"

	def test_staff_reply_on_test_conversation_needs_no_window(self):
		r = self.client.post(f"{BASE}/conversations/{self.ui.id}/staff-reply", {"text": "Staff here"}, format="json")
		assert r.status_code == 201
		self.send.assert_not_called()

	@mock.patch("channels_app.api.schedule_reply")
	def test_test_message_only_for_self_conversations(self, schedule):
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/messages", {"text": "hi"}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "not_a_test_conversation"
		r = self.client.post(f"{BASE}/conversations/{self.ui.id}/messages", {"text": "hi"}, format="json")
		assert r.status_code == 201
		schedule.assert_called_once()

	def test_list_needs_human_filter_order_and_fields(self):
		self.fb.handoff_at = timezone.now()
		self.fb.handoff_reason = "angry"
		self.fb.save()
		r = self.client.get(f"{BASE}/conversations", {"project_slug": self.project.slug})
		data = r.json()["data"]
		assert data["needs_human_count"] == 1
		first = data["conversations"][0]
		assert first["id"] == str(self.fb.id) and first["channel"] == "facebook" and first["contact_display_name"] == "Ann"
		r = self.client.get(f"{BASE}/conversations", {"project_slug": self.project.slug, "needs_human": "1"})
		assert [c["id"] for c in r.json()["data"]["conversations"]] == [str(self.fb.id)]

	def test_messages_include_sender(self):
		self.customer_says("hello")
		r = self.client.get(f"{BASE}/conversations/{self.fb.id}/messages")
		assert r.json()["data"]["messages"][0]["sender"] == "customer"


@mock.patch("agents.api_tool.socket.getaddrinfo", return_value=PUBLIC)
class WebhookApiTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="c@x.com", email="c@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-wh")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user)

	def test_get_creates_with_secret(self, _dns):
		r = self.client.get(f"{BASE}/webhook", {"project_slug": self.project.slug})
		hook = r.json()["data"]["webhook"]
		assert len(hook["secret"]) == 64 and hook["is_active"] is False and hook["url"] == ""

	def test_put_validates_url(self, dns):
		ok = self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "https://hooks.example.com/x", "is_active": True}, format="json")
		assert ok.status_code == 200 and ok.json()["data"]["webhook"]["is_active"] is True
		dns.return_value = PRIVATE
		bad = self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "https://internal.example.com", "is_active": True}, format="json")
		assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_url"
		no_url = self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "", "is_active": True}, format="json")
		assert no_url.status_code == 400

	def test_rotate_secret(self, _dns):
		old = self.client.get(f"{BASE}/webhook", {"project_slug": self.project.slug}).json()["data"]["webhook"]["secret"]
		new = self.client.post(f"{BASE}/webhook/rotate-secret", {"project_slug": self.project.slug}, format="json").json()["data"]["webhook"]["secret"]
		assert new != old and ProjectWebhook.objects.get(project=self.project).secret == new

	@mock.patch("channels_app.webhooks.requests.post")
	def test_send_test_event(self, post, _dns):
		post.return_value = mock.Mock(status_code=204)
		self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "https://hooks.example.com/x", "is_active": True}, format="json")
		r = self.client.post(f"{BASE}/webhook/test", {"project_slug": self.project.slug}, format="json")
		assert r.status_code == 200 and r.json()["data"]["status"] == 204
		assert post.call_args.kwargs["headers"]["X-AMP-Event"] == "ping"
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose run --rm django python manage.py test channels_app.tests.test_handoff_api -v 2`
Expected: failures/errors (404 for new routes, `KeyError` for new fields, `schedule_reply` attribute missing).

- [ ] **Step 3: Imports and helpers in `api.py`**

Replace `from .tasks import handle_ui_message` with:

```python
from datetime import timedelta
import requests
from django.conf import settings
from django.utils import timezone
from .handoff import conversation_event_data, end_handoff, note_staff_activity, start_handoff
from .models import ProjectWebhook
from .reply import deliver, schedule_reply
from .service.message_service import create_outbound_message
from .webhooks import WebhookUrlError, build_event, emit_event, post_event, validate_webhook_url
```

Add after `_get_project`:

```python
def _iso(dt):
	return dt.isoformat() if dt else None


def _conversation_dict(conv: Conversation) -> dict:
	return {
		"id": str(conv.id),
		"name": conv.name or "Unnamed Conversation",
		"knowledge_base_id": str(conv.knowledge_base.id),
		"knowledge_base_name": conv.knowledge_base.name,
		"channel": conv.channel,
		"contact_display_name": conv.contact.display_name if conv.contact else "",
		"handoff_at": _iso(conv.handoff_at),
		"handoff_reason": conv.handoff_reason,
		"is_paused": conv.is_paused(),
		"resumes_at": _iso(conv.resumes_at()),
		"created_at": conv.created_at.isoformat(),
		"updated_at": conv.updated_at.isoformat(),
	}


def _message_dict(msg: Message) -> dict:
	return {
		"id": str(msg.id),
		"direction": msg.direction,
		"sender": msg.sender,
		"status": msg.status,
		"text": msg.text,
		"created_at": msg.created_at.isoformat(),
		"agent_reply_id": str(msg.agent_reply_id) if msg.agent_reply_id else None,
	}


def _error(code: str, message: str, http_status: int) -> Response:
	return Response({"success": False, "error": {"code": code, "message": message}}, status=http_status)


def _paused_cutoff():
	return timezone.now() - timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS)


def _get_conversation(request, conversation_id):
	conversation = Conversation.objects.filter(id=conversation_id).select_related(
		"knowledge_base", "contact", "project"
	).first()
	if conversation:
		_verify_project_access(request.user, conversation.project)
	return conversation
```

- [ ] **Step 4: Use helpers in existing views**
  - `ConversationListView.get`: after building `conversations` queryset, add `if request.query_params.get("needs_human") == "1": conversations = conversations.filter(handoff_at__gte=_paused_cutoff())`. Each item becomes `{**_conversation_dict(conv), "message_count": message_count, "last_message": ...}` (keep existing `last_message` expression). After the loop: `conversations_data.sort(key=lambda c: not c["is_paused"])  # paused first, stable within groups`. Response `data` adds `"needs_human_count": Conversation.objects.filter(project=project, handoff_at__gte=_paused_cutoff()).count()`.
  - `ConversationListView.post`, `ConversationDetailView.get`, `ConversationDetailView.patch`: `"conversation": _conversation_dict(conversation)`.
  - `ConversationMessageListView.get`: `messages_data = [_message_dict(msg) for msg in messages]`.
  - `ConversationMessageListView.post`: after `_verify_project_access(...)` add:

```python
		if conversation.channel != "self":
			return _error("not_a_test_conversation", "Use staff reply for customer conversations", status.HTTP_400_BAD_REQUEST)
```

  and replace `handle_ui_message.delay(str(msg.id))` with `schedule_reply(msg)`; response message → `_message_dict(msg)`.

- [ ] **Step 5: New views** — append to `api.py`:

```python
class ConversationHandoffView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		start_handoff(conversation, (request.data.get("reason") or "").strip() or "paused by staff")
		return Response({"success": True, "data": {"conversation": _conversation_dict(conversation)}})


class ConversationResumeView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		end_handoff(conversation)
		return Response({"success": True, "data": {"conversation": _conversation_dict(conversation)}})


class ConversationStaffReplyView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		text = (request.data.get("text") or "").strip()
		if not 1 <= len(text) <= 2000:
			return _error("invalid_text", "Reply must be 1-2000 characters", status.HTTP_400_BAD_REQUEST)

		last_in = conversation.messages.filter(direction="in").order_by("-created_at").first()
		if conversation.channel == "facebook" and (
			not last_in or timezone.now() - last_in.created_at > timedelta(hours=24)
		):
			return _error(
				"outside_messaging_window",
				"Facebook only allows replies within 24 hours of the customer's last message. Reply from the Meta inbox instead.",
				status.HTTP_400_BAD_REQUEST,
			)

		msg = create_outbound_message(
			project=conversation.project, contact=conversation.contact, text=text,
			conversation=conversation, sender="staff",
		)
		note_staff_activity(conversation)
		delivered = deliver(conversation, msg, last_in)
		emit_event(conversation.project, "message.staff_reply", {
			**conversation_event_data(conversation),
			"message": {"id": str(msg.id), "text": msg.text, "created_at": msg.created_at.isoformat()},
		})
		if not delivered:
			return _error("send_failed", "Saved, but the message could not be delivered to the customer", status.HTTP_502_BAD_GATEWAY)
		return Response({"success": True, "data": {"message": _message_dict(msg)}}, status=status.HTTP_201_CREATED)


def _webhook_dict(hook: ProjectWebhook) -> dict:
	return {"url": hook.url, "is_active": hook.is_active, "secret": hook.secret}


def _project_webhook(request) -> ProjectWebhook:
	slug = request.query_params.get("project_slug") or request.data.get("project_slug")
	if not slug:
		raise ValidationError({"project_slug": "This field is required"})
	project = _get_project(request.user, slug)
	hook, _ = ProjectWebhook.objects.get_or_create(project=project)
	return hook


class ProjectWebhookView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		return Response({"success": True, "data": {"webhook": _webhook_dict(_project_webhook(request))}})

	def put(self, request):
		hook = _project_webhook(request)
		url = (request.data.get("url") or "").strip()
		is_active = request.data.get("is_active") is True
		try:
			if url:
				url = validate_webhook_url(url)
			if is_active and not url:
				raise WebhookUrlError("Add a URL before enabling the webhook")
		except WebhookUrlError as e:
			return _error("invalid_url", str(e), status.HTTP_400_BAD_REQUEST)
		hook.url, hook.is_active = url, is_active
		hook.save(update_fields=["url", "is_active", "updated_at"])
		return Response({"success": True, "data": {"webhook": _webhook_dict(hook)}})


class ProjectWebhookRotateView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		hook = _project_webhook(request)
		hook.secret = ProjectWebhook._meta.get_field("secret").default()
		hook.save(update_fields=["secret", "updated_at"])
		return Response({"success": True, "data": {"webhook": _webhook_dict(hook)}})


class ProjectWebhookTestView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		hook = _project_webhook(request)
		try:
			code = post_event(hook, build_event(hook.project_id, "ping", {"message": "Test event from Auto Magic Pro"}))
		except WebhookUrlError as e:
			return _error("invalid_url", str(e), status.HTTP_400_BAD_REQUEST)
		except requests.RequestException as e:
			return _error("delivery_failed", f"Could not reach the URL ({type(e).__name__})", status.HTTP_502_BAD_GATEWAY)
		return Response({"success": True, "data": {"status": code}})
```

- [ ] **Step 6: Routes** — in `backend/channels_app/urls_api.py` import the five new views from `.api` and add:

```python
	path("conversations/<uuid:conversation_id>/handoff", ConversationHandoffView.as_view(), name="conversation-handoff"),
	path("conversations/<uuid:conversation_id>/resume", ConversationResumeView.as_view(), name="conversation-resume"),
	path("conversations/<uuid:conversation_id>/staff-reply", ConversationStaffReplyView.as_view(), name="conversation-staff-reply"),
	path("webhook", ProjectWebhookView.as_view(), name="project-webhook"),
	path("webhook/rotate-secret", ProjectWebhookRotateView.as_view(), name="project-webhook-rotate"),
	path("webhook/test", ProjectWebhookTestView.as_view(), name="project-webhook-test"),
```

- [ ] **Step 7: Run, verify pass**

Run: `docker compose run --rm django python manage.py test channels_app -v 2`
Expected: all OK except the known pre-existing `test_facebook_webhook.test_get_verification`.

- [ ] **Step 8: Commit**

```bash
git add backend/channels_app/api.py backend/channels_app/urls_api.py backend/channels_app/tests/test_handoff_api.py
git commit -m "feat(api): handoff, resume, staff reply and webhook settings endpoints"
```

---

### Task 7: Frontend — conversations, handoff controls, staff replies

**Files:**
- Modify: `frontend/lib/api.ts` (conversation types + new calls)
- Modify: `frontend/lib/utils.ts` (`timeAgo`, `hoursUntil`)
- Create: `frontend/components/chat/HandoffControls.tsx`
- Modify: `frontend/components/chat/MessageList.tsx` (staff sender)
- Modify: `frontend/app/(dashboard)/conversations/page.tsx`
- Modify: `frontend/app/(dashboard)/conversations/[id]/page.tsx`
- Modify: `frontend/components/layout/Sidebar.tsx`

**Interfaces:**
- Consumes: Task 6 JSON.
- Produces: `Conversation`, `ConversationListItem`, `ConversationMessage` types; `conversationApi.list(slug, opts?)`, `.handoff(id, reason?)`, `.resume(id)`, `.staffReply(id, text)`; `HandoffControls` component.

- [ ] **Step 1: Types and calls in `frontend/lib/api.ts`** — above `export const conversationApi` add:

```ts
export type Conversation = {
	id: string;
	name: string;
	knowledge_base_id: string;
	knowledge_base_name: string;
	channel: string; // "self" (test/organic) | "facebook" | ...
	contact_display_name: string;
	handoff_at: string | null;
	handoff_reason: string;
	is_paused: boolean;
	resumes_at: string | null;
	created_at: string;
	updated_at: string;
};

export type ConversationListItem = Conversation & {
	message_count: number;
	last_message: { id: string; text: string; created_at: string } | null;
};

export type ConversationMessage = {
	id: string;
	direction: "in" | "out";
	sender: "customer" | "bot" | "staff";
	status: "pending" | "processing" | "completed" | "failed";
	text: string;
	created_at: string;
	agent_reply_id: string | null;
};
```

Inside `conversationApi`:
- `list` becomes:

```ts
	list: async (projectSlug: string, opts: { needsHuman?: boolean } = {}) => {
		const params = new URLSearchParams({ project_slug: projectSlug });
		if (opts.needsHuman) params.append("needs_human", "1");
		return apiFetch<{ conversations: ConversationListItem[]; needs_human_count: number }>(
			`/api/channels/conversations?${params.toString()}`
		);
	},
```

- `get`, `create`, `update` response types → `{ conversation: Conversation }`.
- `getMessages` response `messages` type → `ConversationMessage[]` (keep `pagination` as is).
- `sendMessage` response `message` type → `ConversationMessage`.
- Add:

```ts
	handoff: async (conversationId: string, reason?: string) =>
		apiFetch<{ conversation: Conversation }>(`/api/channels/conversations/${conversationId}/handoff`, {
			method: "POST",
			json: reason ? { reason } : {},
		}),

	resume: async (conversationId: string) =>
		apiFetch<{ conversation: Conversation }>(`/api/channels/conversations/${conversationId}/resume`, {
			method: "POST",
			json: {},
		}),

	staffReply: async (conversationId: string, text: string) =>
		apiFetch<{ message: ConversationMessage }>(`/api/channels/conversations/${conversationId}/staff-reply`, {
			method: "POST",
			json: { text },
		}),
```

- [ ] **Step 2: Time helpers** — append to `frontend/lib/utils.ts`:

```ts
export function timeAgo(iso: string): string {
	const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.round(minutes / 60);
	return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

export function hoursUntil(iso: string): number {
	return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 3600000));
}
```

- [ ] **Step 3: `MessageList` staff sender** — in `frontend/components/chat/MessageList.tsx`:
  - `ChatMessage` type gains `sender?: "customer" | "bot" | "staff";`
  - import `Headphones` alongside `User, Bot`.
  - inside the map: `const isStaff = m.sender === "staff";`
  - avatar icon: `{isUser ? <User className="h-4 w-4" /> : isStaff ? <Headphones className="h-4 w-4" /> : <Bot className="h-4 w-4" />}`
  - bubble class for assistant side: `isUser ? "bg-primary text-primary-foreground rounded-br-md" : isStaff ? "bg-amber-50 dark:bg-amber-950/40 text-foreground rounded-bl-md border border-amber-300/60" : "bg-muted text-foreground rounded-bl-md border border-border/50"`
  - above the bubble when staff: `{isStaff && <span className="text-xs text-muted-foreground">Staff</span>}`

- [ ] **Step 4: Create `frontend/components/chat/HandoffControls.tsx`**

```tsx
"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { conversationApi, type Conversation, type ConversationMessage } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { hoursUntil } from "@/lib/utils";

export default function HandoffControls({
	conversation,
	onConversationChange,
	onStaffMessage,
}: {
	conversation: Conversation;
	onConversationChange: (c: Conversation) => void;
	onStaffMessage: (m: ConversationMessage) => void;
}) {
	const [busy, setBusy] = useState(false);
	const [reply, setReply] = useState("");
	const [error, setError] = useState<string | null>(null);

	async function run<T>(fn: () => Promise<T>) {
		setBusy(true);
		setError(null);
		try {
			return await fn();
		} catch (err) {
			setError(formatError(err));
		} finally {
			setBusy(false);
		}
	}

	async function toggleBot() {
		const res = await run(() =>
			conversation.is_paused ? conversationApi.resume(conversation.id) : conversationApi.handoff(conversation.id)
		);
		if (res) onConversationChange(res.data.conversation);
	}

	async function sendStaffReply() {
		const text = reply.trim();
		if (!text) return;
		const res = await run(() => conversationApi.staffReply(conversation.id, text));
		if (res) {
			setReply("");
			onStaffMessage(res.data.message);
			const fresh = await conversationApi.get(conversation.id).catch(() => null);
			if (fresh) onConversationChange(fresh.data.conversation);
		}
	}

	return (
		<div className="border-t px-4 py-3 space-y-3">
			{conversation.is_paused ? (
				<div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm">
					<span>
						<strong>Bot paused</strong>
						{conversation.handoff_reason && <> — {conversation.handoff_reason}</>}.{" "}
						{conversation.channel === "facebook" && "Reply here or in the Meta inbox. "}
						{conversation.resumes_at && <>Auto-resumes in {hoursUntil(conversation.resumes_at)}h.</>}
					</span>
					<Button size="sm" variant="outline" onClick={toggleBot} disabled={busy}>
						Resume bot
					</Button>
				</div>
			) : (
				<div className="flex justify-end">
					<Button size="sm" variant="outline" onClick={toggleBot} disabled={busy}>
						Hand off to staff
					</Button>
				</div>
			)}

			<div className="flex gap-2 items-end">
				<Textarea
					aria-label="Reply as staff"
					placeholder="Reply as staff…"
					rows={2}
					value={reply}
					maxLength={2000}
					onChange={(e) => setReply(e.target.value)}
				/>
				<Button onClick={sendStaffReply} disabled={busy || !reply.trim()}>
					{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Send as staff"}
				</Button>
			</div>
			{error && <p className="text-sm text-destructive">{error}</p>}
		</div>
	);
}
```

- [ ] **Step 5: Conversation page** — in `frontend/app/(dashboard)/conversations/[id]/page.tsx`:
  - Imports: `import HandoffControls from "@/components/chat/HandoffControls";` and change the api import to `import { conversationApi, type Conversation, type ConversationMessage } from "@/lib/api";`. Delete the local `ApiMessage` type.
  - State: `const [conversation, setConversation] = useState<Conversation | null>(null);`
  - Add helper above `fetchMessages`:

```tsx
	function toChatMessage(msg: ConversationMessage): ChatMessage {
		return { id: msg.id, role: msg.direction === "in" ? "user" : "assistant", content: msg.text, sender: msg.sender };
	}

	async function fetchConversation() {
		try {
			const res = await conversationApi.get(conversationId);
			setConversation(res.data.conversation);
		} catch (err) {
			setError(formatError(err));
		}
	}
```

  - In the mount `useEffect`, call `fetchConversation();` next to `fetchMessages();`.
  - In `fetchMessages`, replace the `apiMessages.map((msg) => ({...}))` with `apiMessages.map(toChatMessage)`.
  - In `poll`, after the `if (msg.status === "completed" && msg.agent_reply) { ... }` block's closing brace, insert a new branch before `else if (msg.status === "failed")`:

```tsx
				} else if (msg.status === "completed") {
					// Answered as part of a later message's batch, or bot paused: nothing to add
					const interval = pollingIntervals.current.get(messageId);
					if (interval) {
						clearInterval(interval);
						pollingIntervals.current.delete(messageId);
					}
					setWaitingForReply(pollingIntervals.current.size > 0);
					fetchConversation();
```

  - In the `msg.agent_reply` branch, after adding the reply, call `fetchConversation();` (a handoff may have just happened).
  - Header: replace the `AI Assistant` / `Always here to help` texts with `{conversation?.name || "Conversation"}` and `{conversation?.channel === "facebook" ? "Facebook" : "Test chat"}{conversation?.is_paused ? " · bot paused" : ""}`.
  - Replace `<ChatInput onSend={handleSend} disabled={sending} />` with:

```tsx
			{conversation && (
				<HandoffControls
					conversation={conversation}
					onConversationChange={setConversation}
					onStaffMessage={(m) => setMessages((prev) => [...prev, toChatMessage(m)])}
				/>
			)}
			{conversation?.channel === "self" && <ChatInput onSend={handleSend} disabled={sending} />}
```

- [ ] **Step 6: Conversations list** — in `frontend/app/(dashboard)/conversations/page.tsx`:
  - Delete the local `Conversation` type; import `type ConversationListItem` from `@/lib/api`, plus `Badge` from `@/components/ui/badge` and `timeAgo` from `@/lib/utils`.
  - State: `useState<ConversationListItem[]>([])`; add `const [needsHumanOnly, setNeedsHumanOnly] = useState(false);` and `const [needsHumanCount, setNeedsHumanCount] = useState(0);`.
  - `fetchConversations`: `const response = await conversationApi.list(currentProject.slug, { needsHuman: needsHumanOnly }); setConversations(response.data.conversations); setNeedsHumanCount(response.data.needs_human_count);`
  - The fetch `useEffect` dependency list adds `needsHumanOnly`.
  - In the header `div` next to "New Conversation" add:

```tsx
					<Button
						variant={needsHumanOnly ? "default" : "outline"}
						onClick={() => setNeedsHumanOnly((v) => !v)}
					>
						Needs human ({needsHumanCount})
					</Button>
```

  - Replace the name line `<div className="font-medium">{conv.name || "Unnamed Conversation"}</div>` with:

```tsx
										<div className="font-medium flex flex-wrap items-center gap-2">
											{conv.name || "Unnamed Conversation"}
											<Badge variant="outline">{conv.channel === "self" ? "Test" : conv.channel === "facebook" ? "Facebook" : conv.channel}</Badge>
											{conv.is_paused && <Badge variant="destructive">Needs human</Badge>}
										</div>
										{conv.is_paused && conv.handoff_at && (
											<div className="text-xs text-destructive">
												{conv.handoff_reason || "Handed off"} • {timeAgo(conv.handoff_at)}
											</div>
										)}
```

- [ ] **Step 7: Sidebar count** — in `frontend/components/layout/Sidebar.tsx`:
  - imports: `import { useAuth } from "@/hooks/useAuth";` and `import { conversationApi } from "@/lib/api";`
  - inside the component, right after the `isMounted` state (before any early return):

```tsx
	const { projects } = useAuth();
	const [needsHuman, setNeedsHuman] = useState(0);
	const projectSlug = projects && projects.length > 0 ? projects[0].slug : "";

	// ponytail: refreshed on navigation only; add polling/websocket if staff need live counts
	useEffect(() => {
		if (!projectSlug) return;
		conversationApi
			.list(projectSlug, { needsHuman: true })
			.then((r) => setNeedsHuman(r.data.needs_human_count))
			.catch(() => {});
	}, [projectSlug, pathname]);
```

  - after `{!isCollapsed && (<span className="truncate">{l.label}</span>)}` add:

```tsx
									{l.href === "/conversations" && needsHuman > 0 && (
										<span
											className={cn(
												"rounded-full bg-destructive px-1.5 text-xs font-semibold text-destructive-foreground",
												!isCollapsed && "ml-auto"
											)}
											aria-label={`${needsHuman} conversations need a human`}
										>
											{needsHuman}
										</span>
									)}
```

- [ ] **Step 8: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: exit 0; no new warnings in changed files.

- [ ] **Step 9: Commit**

```bash
git add frontend/lib/api.ts frontend/lib/utils.ts frontend/components/chat/HandoffControls.tsx frontend/components/chat/MessageList.tsx "frontend/app/(dashboard)/conversations/page.tsx" "frontend/app/(dashboard)/conversations/[id]/page.tsx" frontend/components/layout/Sidebar.tsx
git commit -m "feat(ui): handoff controls, staff replies, needs-human list and sidebar count"
```

---

### Task 8: Frontend — webhook settings card

**Files:**
- Modify: `frontend/lib/api.ts` (add `webhookApi`)
- Create: `frontend/components/channels/WebhookSettings.tsx`
- Modify: `frontend/app/(dashboard)/channels/page.tsx`

**Interfaces:**
- Consumes: Task 6 webhook endpoints.
- Produces: `webhookApi.get/update/rotate/test`, `WebhookSettings({ projectSlug })`.

- [ ] **Step 1: `webhookApi`** — append to `frontend/lib/api.ts`:

```ts
export type ProjectWebhook = { url: string; is_active: boolean; secret: string };

export const webhookApi = {
	get: async (projectSlug: string) =>
		apiFetch<{ webhook: ProjectWebhook }>(`/api/channels/webhook?project_slug=${encodeURIComponent(projectSlug)}`),

	update: async (projectSlug: string, data: { url: string; is_active: boolean }) =>
		apiFetch<{ webhook: ProjectWebhook }>("/api/channels/webhook", {
			method: "PUT",
			json: { project_slug: projectSlug, ...data },
		}),

	rotate: async (projectSlug: string) =>
		apiFetch<{ webhook: ProjectWebhook }>("/api/channels/webhook/rotate-secret", {
			method: "POST",
			json: { project_slug: projectSlug },
		}),

	test: async (projectSlug: string) =>
		apiFetch<{ status: number }>("/api/channels/webhook/test", {
			method: "POST",
			json: { project_slug: projectSlug },
		}),
};
```

- [ ] **Step 2: Create `frontend/components/channels/WebhookSettings.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { Copy, Eye, EyeOff, Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { webhookApi, type ProjectWebhook } from "@/lib/api";
import { formatError } from "@/lib/errors";

export default function WebhookSettings({ projectSlug }: { projectSlug: string }) {
	const [hook, setHook] = useState<ProjectWebhook | null>(null);
	const [url, setUrl] = useState("");
	const [active, setActive] = useState(false);
	const [showSecret, setShowSecret] = useState(false);
	const [busy, setBusy] = useState(false);
	const [notice, setNotice] = useState<{ type: "ok" | "error"; text: string } | null>(null);

	useEffect(() => {
		webhookApi
			.get(projectSlug)
			.then((r) => {
				setHook(r.data.webhook);
				setUrl(r.data.webhook.url);
				setActive(r.data.webhook.is_active);
			})
			.catch((err) => setNotice({ type: "error", text: formatError(err) }));
	}, [projectSlug]);

	async function run(fn: () => Promise<void>) {
		setBusy(true);
		setNotice(null);
		try {
			await fn();
		} catch (err) {
			setNotice({ type: "error", text: formatError(err) });
		} finally {
			setBusy(false);
		}
	}

	const save = () =>
		run(async () => {
			const r = await webhookApi.update(projectSlug, { url: url.trim(), is_active: active });
			setHook(r.data.webhook);
			setNotice({ type: "ok", text: "Webhook saved" });
		});

	const rotate = () =>
		run(async () => {
			if (!confirm("Rotate the signing secret? Systems verifying the old secret will reject new events.")) return;
			const r = await webhookApi.rotate(projectSlug);
			setHook(r.data.webhook);
			setShowSecret(true);
			setNotice({ type: "ok", text: "Secret rotated" });
		});

	const sendTest = () =>
		run(async () => {
			const r = await webhookApi.test(projectSlug);
			setNotice({ type: "ok", text: `Test event delivered — your endpoint answered HTTP ${r.data.status}` });
		});

	return (
		<Card>
			<CardHeader>
				<CardTitle>Integrations — webhooks</CardTitle>
				<CardDescription>
					We POST signed JSON events to this URL: conversation.handoff, conversation.resumed and message.staff_reply.
					Verify the X-AMP-Signature header (HMAC-SHA256 of the raw body with the secret below).
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				{!hook ? (
					<Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
				) : (
					<>
						<div className="space-y-1">
							<Label htmlFor="webhook-url">Endpoint URL</Label>
							<Input
								id="webhook-url"
								placeholder="https://hooks.zapier.com/hooks/catch/…"
								value={url}
								onChange={(e) => setUrl(e.target.value)}
							/>
						</div>
						<label className="flex items-center gap-2 text-sm">
							<input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
							Send events
						</label>
						<div className="space-y-1">
							<Label>Signing secret</Label>
							<div className="flex gap-2">
								<Input readOnly value={showSecret ? hook.secret : "•".repeat(24)} className="font-mono" />
								<Button
									type="button"
									variant="outline"
									size="icon"
									aria-label={showSecret ? "Hide secret" : "Show secret"}
									onClick={() => setShowSecret((v) => !v)}
								>
									{showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
								</Button>
								<Button
									type="button"
									variant="outline"
									size="icon"
									aria-label="Copy secret"
									onClick={() => navigator.clipboard?.writeText(hook.secret)}
								>
									<Copy className="h-4 w-4" />
								</Button>
							</div>
						</div>
						<div className="flex flex-wrap gap-2">
							<Button onClick={save} disabled={busy}>Save</Button>
							<Button variant="outline" onClick={sendTest} disabled={busy || !hook.url}>Send test event</Button>
							<Button variant="ghost" onClick={rotate} disabled={busy}>Rotate secret</Button>
						</div>
						{notice && (
							<p className={notice.type === "ok" ? "text-sm text-green-600 dark:text-green-400" : "text-sm text-destructive"}>
								{notice.text}
							</p>
						)}
					</>
				)}
			</CardContent>
		</Card>
	);
}
```

- [ ] **Step 3: Render on channels page** — in `frontend/app/(dashboard)/channels/page.tsx` add `import WebhookSettings from "@/components/channels/WebhookSettings";` and, as the last child inside the top-level `<div className="space-y-6">` (just before its closing `</div>`, after the delete `Dialog`):

```tsx
			{projectSlug && <WebhookSettings projectSlug={projectSlug} />}
```

- [ ] **Step 4: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: exit 0.

- [ ] **Step 5: Manual check** (stack up: `docker compose up -d`, `cd frontend && npm run dev`, celery worker running)
  1. Test chat: send "can I talk to a person" → reply is the handoff message; banner "Bot paused — customer asked for a person"; Conversations list shows **Needs human** first; sidebar shows 1.
  2. In the paused test chat send another message → no bot reply, spinner stops.
  3. Type a staff reply → appears with "Staff" label; Resume bot → banner gone, next message gets a bot reply.
  4. Facebook: message the page "hi" then "price?" quickly → one reply covering both. Ask for a refund → handoff. Reply as staff from the dashboard → arrives in Messenger.
  5. Webhooks: paste a https://webhook.site URL, enable, Save, Send test event → ping arrives with X-AMP-Signature; trigger a handoff → `conversation.handoff` arrives. Enter `http://localhost:9000` → save shows "not a public address".

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api.ts frontend/components/channels/WebhookSettings.tsx "frontend/app/(dashboard)/channels/page.tsx"
git commit -m "feat(ui): webhook integration settings"
```
