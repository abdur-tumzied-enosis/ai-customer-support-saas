# Audio Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transcribe every inbound voice note to text, answer it through the normal reply pipeline, and bill it at 1 credit per started 5 seconds of audio.

**Architecture:** `store_message_media` keeps only storage and hands each audio attachment to its own task. That task measures the duration, charges credits **before** calling OpenRouter (so the charge is the reservation), writes an attempt `UsageEvent` before the HTTP call, and on success appends the transcript to `Message.text`. Every attachment ends terminal, then one shared check schedules the reply. Per-message state lives in `Message.meta["transcription"]` under a row lock.

**Tech Stack:** Django 5, DRF, Celery, Postgres (JSONB), Redis, `requests`, `tinytag==2.1.1` (new, MIT), OpenRouter `POST /api/v1/audio/transcriptions`.

**Spec:** `docs/superpowers/specs/2026-11-14-audio-transcription-design.md` (rev 4; see its "Changes from revision 3" for what this revision of the plan fixes)

## Global Constraints

- Indentation is **tabs** everywhere in `backend/`. Match the surrounding file.
- Money and credits are `Decimal`; `billing.service` already quantizes to `CENT`. Never use floats for credits.
- Every customer-facing charge goes through `billing.service`; every charge carries a stable idempotency `key`.
- Never log transcript text, audio bytes or audio URLs. Log message ids, attachment indexes and exception type names only.
- Tests run in Docker, never a local venv: `docker compose exec django python manage.py test <dotted.path> --noinput`.
- New settings use `django-environ` (`env.bool`, `env.list`, `env.int`, `env`).
- `TRANSCRIPTION_ENABLED` defaults to **False**; nothing in this plan may flip it.
- One charge per attachment, key `transcription:{message_id}:{attachment_index}`; one refund per attachment, key `transcription_refund:{message_id}:{attachment_index}`.
- Pricing constants: `transcription_seconds_per_credit = 5`, minimum 1 unit, `TRANSCRIPTION_MAX_SECONDS = 300`.
- `units = max(1, ceil(seconds / seconds_per_credit))`. The price is applied **once**, inside the charge. Never multiply by `price_of()` in the caller.
- A refund amount is always `-txn.amount` of the charge row found by its key. No charge row → no refund.
- The audio format comes from the bytes (`audio_format`), never from the stored file's extension: Messenger/Instagram audio is stored as `.bin`.
- Test fixtures are our own recorded voice notes. Never copy files out of `chat_media/` (customer audio).
- Supported models, verbatim: `elevenlabs/scribe-v2`, `x-ai/grok-stt-2.0`, `google/gemini-3.5-transcribe`. Default `elevenlabs/scribe-v2`.

## Review Focus

These are the conditions most likely to reach real customers and are not obvious from any single task's tests. Each has a test in the task that owns the code.

1. **A 200 response with empty `text`** (silence, or the model returning only whitespace). Must not bill. Test in Task 6.
2. **A worker killed between the charge and the provider call.** Money taken, no service. Must be refunded by the sweep. Test in Task 9.
3. **A voice note arriving while the bot is generating a reply.** The turn must not be dropped and must not be answered twice. Test in Task 8.
4. **A text arriving while a voice note transcribes.** One reply covering both, scheduled when the transcription ends, not 5 minutes later by recovery. Test in Task 8.
5. **A project with almost no credits sending two voice notes at once.** Exactly one may pass. Test in Task 1.
6. **A retry of an attachment already charged, on a wallet the charge itself drained.** Must return the existing charge, not refuse it and strand it. Test in Task 1.
7. **Messenger and Instagram voice notes**, stored as `.bin`. Must be transcribed from their bytes. Test in Tasks 2 and 6.

---

### Task 0: Real fixtures and real provider responses (no code)

The spec requires one real response per model **before coding**. Task 2's parser is written against these files, not against a guessed shape.

**Files:**
- Create (done 2026-11-14): `backend/agents/tests/fixtures/voice.ogg`, `backend/agents/tests/fixtures/voice_messenger.mp3` (the Messenger sample is MP3; no MP4 was supplied)
- Create (done): `backend/agents/tests/fixtures/openrouter_scribe-v2.json`, `openrouter_grok-stt-2.0.json`, `openrouter_gemini-3.5-transcribe.json`, plus `_messenger` variants for the MP3 sample

- [ ] **Step 1: Record the audio.** From your own phone, send a 3–8 second voice note to the dev WhatsApp number and one to the dev Messenger page. Say a short neutral sentence. Download the two stored files from dev R2 (`chat_media/<dev project slug>/…`) and save them under the names above. These are your recordings, so they may be committed. **Do not use any other file from `chat_media/`.**
- [ ] **Step 2: Note the containers.** Done: `voice.ogg` starts `OggS`; `voice_messenger.mp3` starts `ID3`. Still needed for the format matrix: one MP4/M4A voice note (for example, record a voice note through the Messenger flow in a format that is not MP3, or send one from an Instagram DM), with its `ftyp` brand noted. Until it exists, the M4A row of the matrix is open and Task 2 stays on the MP3 and Ogg paths.
- [x] **Step 3: Capture one response per model.** Done 2026-11-14 with the project's configured key, run inside the container so the key was never printed. Six calls, all HTTP 200.
- [x] **Step 4: Fix the field names.** Done. Findings (also in the spec's Constraints): `text` at top level on all three; `usage.cost` on all three; tokens are `input_tokens`/`output_tokens`/`total_tokens` on Gemini only, and absent on scribe and grok; `usage.seconds` on scribe and grok. Task 2's parser and tests are corrected below to match.
- [ ] **Step 5: Commit.**

```bash
git add backend/agents/tests/fixtures
git commit -m "test(agents): recorded voice fixtures and captured transcription responses"
```

---

### Task 1: `charge_if_affordable` — atomic check and charge

**Files:**
- Modify: `backend/billing/service.py` (add one function after `charge`)
- Test: `backend/billing/tests/test_service.py`

**Interfaces:**
- Produces: `charge_if_affordable(project, action, *, key: str, units: int = 1, conversation=None, trigger_message=None, result_message=None) -> CreditTransaction | None` — returns the new or existing transaction; `None` only for a zero-priced action (same as `charge`); **raises `InsufficientCredits`** when a new charge would pass `-PricingSettings.get().overdraft_credits`.
- Consumes: existing `price_of`, `_lock`, `_post`, `PricingSettings`, `InsufficientCredits`.

- [ ] **Step 1: Write the failing tests**

In `backend/billing/tests/test_service.py`, add:

```python
class ChargeIfAffordableTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-afford")

	def test_charges_when_balance_covers_it(self):
		service.grant(self.project, Decimal("10"), "adjust", key="seed")
		txn = service.charge_if_affordable(self.project, "reply", key="a", units=2)
		assert txn.amount == Decimal("-2.00")
		assert service.balance_of(self.project) == Decimal("8.00")

	def test_raises_past_the_overdraft_and_writes_nothing(self):
		PricingSettings.objects.filter(pk=1).update(overdraft_credits=Decimal("1"))
		with self.assertRaises(service.InsufficientCredits):
			service.charge_if_affordable(self.project, "reply", key="a", units=2)
		assert not CreditTransaction.objects.filter(idempotency_key="a").exists()
		assert service.balance_of(self.project) == Decimal("0")

	def test_zero_price_returns_none_and_is_not_unaffordable(self):
		ActionPrice.objects.filter(action="reply").update(credits=Decimal("0"))
		PricingSettings.objects.filter(pk=1).update(overdraft_credits=Decimal("0"))
		assert service.charge_if_affordable(self.project, "reply", key="a", units=3) is None
		assert not CreditTransaction.objects.filter(idempotency_key="a").exists()

	def test_retry_of_a_charge_that_drained_the_wallet_returns_it(self):
		# the first charge lowered the balance; a retry must find it, not refuse it as unaffordable
		PricingSettings.objects.filter(pk=1).update(overdraft_credits=Decimal("0"))
		service.grant(self.project, Decimal("10"), "adjust", key="seed")
		first = service.charge_if_affordable(self.project, "reply", key="a", units=8)
		second = service.charge_if_affordable(self.project, "reply", key="a", units=8)
		assert second.pk == first.pk
		assert service.balance_of(self.project) == Decimal("2.00")

	def test_allows_the_overdraft_floor_itself(self):
		PricingSettings.objects.filter(pk=1).update(overdraft_credits=Decimal("5"))
		assert service.charge_if_affordable(self.project, "reply", key="a", units=5) is not None
		assert service.balance_of(self.project) == Decimal("-5.00")

	def test_repeated_key_returns_the_existing_row(self):
		service.grant(self.project, Decimal("10"), "adjust", key="seed")
		first = service.charge_if_affordable(self.project, "reply", key="a", units=2)
		second = service.charge_if_affordable(self.project, "reply", key="a", units=2)
		assert second.pk == first.pk
		assert service.balance_of(self.project) == Decimal("8.00")
```

And a race proof in a new file `backend/billing/tests/test_charge_race.py`:

```python
import threading
from decimal import Decimal

from django.db import connections
from django.test import TransactionTestCase

from accounts.models import Project
from billing import service


class ChargeIfAffordableRaceTest(TransactionTestCase):
	def test_two_concurrent_charges_cannot_both_pass(self):
		project = Project.objects.create(name="P", slug="p-race")
		service.grant(project, Decimal("60"), "adjust", key="seed")   # room for one 60-unit charge, not two
		barrier, results = threading.Barrier(2), []

		def worker(key):
			try:
				barrier.wait()
				results.append(service.charge_if_affordable(project, "reply", key=key, units=60))
			except service.InsufficientCredits:
				results.append(None)
			finally:
				connections.close_all()

		threads = [threading.Thread(target=worker, args=(k,)) for k in ("a", "b")]
		for t in threads: t.start()
		for t in threads: t.join()
		assert len([r for r in results if r is not None]) == 1
		assert service.balance_of(project) == Decimal("0.00")
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test billing.tests.test_service billing.tests.test_charge_race --noinput`
Expected: FAIL — `AttributeError: module 'billing.service' has no attribute 'charge_if_affordable'`.

- [ ] **Step 3: Implement in `backend/billing/service.py`**

```python
def charge_if_affordable(project, action, *, key: str, units: int = 1, conversation=None, trigger_message=None, result_message=None):
	"""Charge only if the result stays within the overdraft. The check and the write share one wallet lock,
	so two concurrent callers cannot both pass. A repeated key returns the existing row before any check.
	Returns None for a zero-priced action; raises InsufficientCredits when unaffordable."""
	price = price_of(action)
	if price == 0:
		return None
	amount = price * units
	with transaction.atomic():
		wallet = _lock(project)
		existing = CreditTransaction.objects.filter(idempotency_key=key).first()
		if existing:  # before the check: this charge may be what lowered the balance
			return existing
		if wallet.balance - amount < -PricingSettings.get().overdraft_credits:
			raise InsufficientCredits(amount, wallet.balance)
		return _post(
			wallet, -amount, kind="charge", action=action, idempotency_key=key,
			conversation=conversation, trigger_message=trigger_message, result_message=result_message,
		)
```

The order is the point: zero price, lock, idempotency lookup, affordability, post.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test billing.tests.test_service billing.tests.test_charge_race --noinput`
Expected: PASS. If `test_charge_race` is flaky, confirm the test database is Postgres, not SQLite — `select_for_update` does nothing on SQLite.

- [ ] **Step 5: Commit**

```bash
git add backend/billing/service.py backend/billing/tests/test_service.py backend/billing/tests/test_charge_race.py
git commit -m "feat(billing): atomic affordability check and charge"
```

---

### Task 2: Transcription provider module

**Files:**
- Create: `backend/agents/transcription.py`
- Create: `backend/agents/tests/test_transcription.py`
- Uses (from Task 0): `backend/agents/tests/fixtures/voice.ogg`, `voice_messenger.mp4`, `openrouter_*.json`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces:
  - `SUPPORTED_TRANSCRIPTION_MODELS = ("elevenlabs/scribe-v2", "x-ai/grok-stt-2.0", "google/gemini-3.5-transcribe")`
  - `DEFAULT_TRANSCRIPTION_MODEL = "elevenlabs/scribe-v2"`
  - `class TranscriptionError(Exception)`
  - `@dataclass Transcript(text: str, tokens: int, cost_usd: Decimal | None, model: str, usage: dict)`
  - `audio_format(data: bytes) -> str | None` — `"ogg" | "m4a" | "mp3" | "wav"`, `None` for anything else (AMR included)
  - `audio_seconds(data: bytes, fmt: str) -> int | None`
  - `resolve_transcription_model() -> str`
  - `transcribe(data: bytes, fmt: str, model: str | None = None) -> Transcript`
- Consumes: `settings.OPENROUTER_API_KEY`, `agents.llm.active_model("transcription")`.

**Fixture prerequisite:** Task 0 is done and its field names are reflected in the tests below.

- [ ] **Step 1: Write the failing tests**

`backend/agents/tests/test_transcription.py`:

```python
import base64
import io
import json
import math
import wave
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from agents import transcription
from agents.models import AIModel, ModelConfig

FIXTURES = Path(__file__).parent / "fixtures"


def wav_bytes(seconds: float, rate: int = 8000) -> bytes:
	buf = io.BytesIO()
	with wave.open(buf, "wb") as w:
		w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
		w.writeframes(b"\x00\x00" * int(rate * seconds))
	return buf.getvalue()


class AudioFormatTest(TestCase):
	def test_recorded_fixtures(self):
		assert transcription.audio_format((FIXTURES / "voice.ogg").read_bytes()) == "ogg"
		assert transcription.audio_format((FIXTURES / "voice_messenger.mp3").read_bytes()) == "mp3"

	def test_any_mp4_brand_is_m4a(self):
		# tinytag's own sniff only knows the M4A brand; a Messenger clip may say isom/mp42
		assert transcription.audio_format(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20) == "m4a"

	def test_mp3_and_wav(self):
		assert transcription.audio_format(b"ID3\x04" + b"\x00" * 20) == "mp3"
		assert transcription.audio_format(b"\xff\xfb\x90\x00" + b"\x00" * 20) == "mp3"
		assert transcription.audio_format(wav_bytes(1)) == "wav"

	def test_adts_aac_is_not_mp3(self):
		assert transcription.audio_format(b"\xff\xf1\x50\x80" + b"\x00" * 20) is None

	def test_garbage_empty_and_amr_are_none(self):
		for data in (b"not audio", b"", b"#!AMR\n" + b"\x00" * 40):
			assert transcription.audio_format(data) is None


class AudioSecondsTest(TestCase):
	def test_reads_recorded_fixtures(self):
		for name, fmt in (("voice.ogg", "ogg"), ("voice_messenger.mp3", "mp3")):
			seconds = transcription.audio_seconds((FIXTURES / name).read_bytes(), fmt)
			assert seconds is not None, name
			assert 1 <= seconds <= 10, name

	def test_rounds_up_to_a_whole_second(self):
		assert transcription.audio_seconds(wav_bytes(2.1), "wav") == 3
		assert transcription.audio_seconds(wav_bytes(2.0), "wav") == 2

	def test_unreadable_is_none(self):
		assert transcription.audio_seconds(b"not audio", "ogg") is None
		assert transcription.audio_seconds(b"", "m4a") is None


class ResolveModelTest(TestCase):
	def test_default_is_scribe(self):
		assert transcription.resolve_transcription_model() == "elevenlabs/scribe-v2"

	def test_model_config_wins(self):
		row = AIModel.objects.create(provider="openrouter", name="x-ai/grok-stt-2.0", kind="transcription")
		ModelConfig.objects.update_or_create(pk=1, defaults={"transcription_model": row})
		assert transcription.resolve_transcription_model() == "x-ai/grok-stt-2.0"

	@override_settings(TRANSCRIPTION_MODEL="nope/unknown")
	def test_unsupported_model_falls_back(self):
		assert transcription.resolve_transcription_model() == "elevenlabs/scribe-v2"


class TranscribeTest(TestCase):
	def _post(self, body):
		resp = mock.Mock(status_code=200, text="")
		resp.json.return_value = body
		return mock.patch("agents.transcription.requests.post", return_value=resp)

	def test_posts_base64_with_resolved_model_and_format(self):
		data = (FIXTURES / "voice.ogg").read_bytes()
		with self._post({"text": "hello", "usage": {"cost": 0.0012, "prompt_tokens": 7}}) as post:
			out = transcription.transcribe(data, "ogg")
		payload = post.call_args.kwargs["json"]
		assert payload["model"] == "elevenlabs/scribe-v2"
		assert payload["input_audio"] == {"data": base64.b64encode(data).decode(), "format": "ogg"}
		assert (out.text, out.tokens, out.cost_usd) == ("hello", 7, Decimal("0.0012"))

	def test_parses_each_captured_response(self):
		# the Task 0 captures; adjust the expected fields to what they actually contain
		for name in ("scribe-v2", "grok-stt-2.0", "gemini-3.5-transcribe"):
			body = json.loads((FIXTURES / f"openrouter_{name}.json").read_text())
			with self._post(body):
				out = transcription.transcribe(b"x", "ogg")
			assert out.text.strip(), name
			assert out.cost_usd is None or out.cost_usd > 0, name

	def test_missing_cost_is_none(self):
		with self._post({"text": "hi", "usage": {"prompt_tokens": 3}}):
			assert transcription.transcribe(b"x", "mp3").cost_usd is None

	def test_non_200_raises(self):
		resp = mock.Mock(status_code=500, text="boom")
		with mock.patch("agents.transcription.requests.post", return_value=resp):
			with self.assertRaises(transcription.TranscriptionError):
				transcription.transcribe(b"x", "mp3")

	def test_each_supported_model_is_sent_as_given(self):
		for model in transcription.SUPPORTED_TRANSCRIPTION_MODELS:
			with self._post({"text": "ok", "usage": {}}) as post:
				transcription.transcribe(b"x", "ogg", model=model)
			assert post.call_args.kwargs["json"]["model"] == model

	def test_response_body_is_never_logged(self):
		with self._post({"text": "secret words", "usage": {"cost": 0.001}}), \
		     mock.patch("agents.transcription.logger") as log:
			transcription.transcribe(b"x", "ogg")
		assert "secret words" not in str([c.args for c in log.mock_calls])
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_transcription --noinput`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.transcription'`.

- [ ] **Step 3: Add `tinytag` and implement `backend/agents/transcription.py`**

Add `tinytag==2.1.1` to `requirements.txt`, then rebuild: `docker compose build django && docker compose up -d django`.

Module contents:
- `BASE_URL = "https://openrouter.ai/api/v1/audio/transcriptions"`, `TIMEOUT = 45`.
- `audio_format`:

```python
def audio_format(data: bytes) -> str | None:
	"""Container from the first bytes; the stored extension is unreliable (Messenger/Instagram audio lands as .bin)."""
	if data[:4] == b"OggS":
		return "ogg"
	if data[4:8] == b"ftyp":  # any brand: tinytag's own sniff only accepts M4A/M4B
		return "m4a"
	if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE6 == 0xE2):  # sync + layer III (ADTS AAC has layer 0)
		return "mp3"
	if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
		return "wav"
	return None  # AMR and anything unknown: unmeasurable
```

- `audio_seconds`: `TinyTag.get(filename=f"audio.{fmt}", file_obj=io.BytesIO(data))` (verified against tinytag 2.1.1: the bytes go in `file_obj=`, the filename only picks the parser), `duration = tag.duration or 0`, return `math.ceil(duration)` when `duration > 0` else `None`; any exception → `None`.
- `resolve_transcription_model`: `agents.llm.active_model("transcription")` → `(provider, name)`, else `settings.TRANSCRIPTION_MODEL`, else `DEFAULT`; return the name only when it is in `SUPPORTED_TRANSCRIPTION_MODELS`, otherwise log a warning (name only) and return `DEFAULT`.
- `transcribe`: `model = model or resolve_transcription_model()`; POST `{"model", "input_audio": {"data": b64, "format": fmt}}` with the bearer key; non-200 or invalid JSON → `TranscriptionError` carrying the status code only; read `text` (default `""`) and `usage` (default `{}`); `tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("total_tokens") or 0` (scribe and grok return none, so 0; Gemini returns `input_tokens`); `cost_usd = Decimal(str(usage["cost"])) if usage.get("cost") is not None else None`; return `Transcript(text, int(tokens), cost_usd, model, usage)`. The provider's `usage.seconds` stays inside `usage` for reconciliation and is never used for the charge.
- Log only the status code and exception type; never the body.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test agents.tests.test_transcription --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/agents/transcription.py backend/agents/tests/test_transcription.py
git commit -m "feat(agents): OpenRouter transcription client with local duration"
```

---

### Task 3: Billing action, price knob and public pricing row

**Files:**
- Modify: `backend/billing/models.py` (`ACTIONS`, `PricingSettings`)
- Create: `backend/billing/migrations/0006_transcription_price.py`
- Modify: `backend/billing/api.py` (`PricingView`)
- Test: `backend/billing/tests/test_api.py`

**Interfaces:**
- Produces: `ACTIONS["transcription"] = ("Voice note transcription", Decimal("1"))`; `PricingSettings.transcription_seconds_per_credit` (Decimal, default 5); the pricing endpoint's transcription row carries `"per_seconds": 5`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

In `backend/billing/tests/test_api.py`:

```python
class TranscriptionPriceTest(BillingApiBase):
	def test_action_row_seeded_at_one_credit(self):
		row = ActionPrice.objects.get(action="transcription")
		assert row.credits == Decimal("1")

	def test_pricing_row_carries_per_seconds(self):
		res = self.client.get("/api/billing/pricing/")
		row = next(a for a in res.json()["data"]["actions"] if a["action"] == "transcription")
		assert row["per_seconds"] == 5
		assert row["credits"] == "1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test billing.tests.test_api.TranscriptionPriceTest --noinput`
Expected: FAIL — `ActionPrice.DoesNotExist: ActionPrice matching query does not exist.`

- [ ] **Step 3: Implement**

- `billing/models.py`: add the `ACTIONS` entry; add
  `transcription_seconds_per_credit = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("5"))`
  to `PricingSettings` beside `usd_per_credit`.
- Migration `0006_transcription_price.py`: `AddField` for the new setting plus a `RunPython` that
  `ActionPrice.objects.get_or_create(action="transcription", defaults={"credits": Decimal("1")})`
  (reverse: delete that row). Mirror the style of `0002_seed`.
- `billing/api.py` `PricingView`: read `cfg = PricingSettings.get()` once and add
  `"per_seconds": int(cfg.transcription_seconds_per_credit)` to the row whose `action == "transcription"`.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test billing --noinput`
Expected: PASS, including the existing `test_service` check that every `ACTIONS` entry has an `ActionPrice` row.

- [ ] **Step 5: Commit**

```bash
git add backend/billing/models.py backend/billing/migrations/0006_transcription_price.py backend/billing/api.py backend/billing/tests/test_api.py
git commit -m "feat(billing): price voice notes per started 5 seconds"
```

---

### Task 4: Transcription catalogue rows, model validation and price sync

**Files:**
- Create: `backend/agents/migrations/0007_seed_transcription_models.py`
- Modify: `backend/agents/models.py` (`ModelConfig.clean`, transcription field `help_text`)
- Modify: `backend/agents/pricing.py`
- Test: `backend/agents/tests/test_model_config.py`, `backend/agents/tests/test_pricing_sync.py`

**Interfaces:**
- Consumes: `agents.transcription.SUPPORTED_TRANSCRIPTION_MODELS`.
- Produces: `AIModel` rows for the three supported models; `sync_openrouter_prices()` now also prices models listed only under `output_modalities=transcription`.

- [ ] **Step 1: Write the failing tests**

In `backend/agents/tests/test_model_config.py`:

```python
class TranscriptionModelValidationTest(TestCase):
	def test_supported_model_saves(self):
		row = AIModel.objects.create(provider="openrouter", name="elevenlabs/scribe-v2", kind="transcription")
		ModelConfig(pk=1, transcription_model=row).full_clean()

	def test_unsupported_model_is_rejected(self):
		row = AIModel.objects.create(provider="openrouter", name="vendor/old-asr", kind="transcription")
		with self.assertRaises(ValidationError):
			ModelConfig(pk=1, transcription_model=row).full_clean()
```

In `backend/agents/tests/test_pricing_sync.py`:

```python
	@mock.patch("agents.pricing.requests.get")
	def test_transcription_only_model_is_priced(self, get):
		asr = AIModel.objects.create(provider="openrouter", name="elevenlabs/scribe-v2", kind="transcription")
		get.side_effect = [
			fake_response([{"id": "vendor/cheap", "pricing": {"prompt": "0.0000005", "completion": "0.0000015"}}]),
			fake_response([{"id": "elevenlabs/scribe-v2", "pricing": {"prompt": "0.0000611111111111", "completion": "0"}}]),
		]
		result = sync_openrouter_prices()
		asr.refresh_from_db()
		assert asr.input_price_per_mtok == Decimal("61.111111")
		assert "elevenlabs/scribe-v2" not in result["not_found"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test agents.tests.test_model_config agents.tests.test_pricing_sync --noinput`
Expected: FAIL — no `ValidationError` raised; the ASR row reported in `not_found`.

- [ ] **Step 3: Implement**

- Migration `0007_seed_transcription_models.py`: `get_or_create(provider="openrouter", name=..., defaults={"kind": "transcription"})` for the three supported names. Keep the three older rows untouched.
- `ModelConfig.clean()`: after the vision check, if `self.transcription_model_id` and its `name` is not in `SUPPORTED_TRANSCRIPTION_MODELS`, raise `ValidationError({"transcription_model": "Pick a supported transcription model: ..."})`. Update the field's `help_text` to name the three.
- `agents/pricing.py`: fetch the default list and `f"{OPENROUTER_MODELS_URL}?output_modalities=transcription"`, merge the two `data` arrays into the `entries` dict (a duplicate id takes the transcription entry). Keep one `raise_for_status` per request; either request failing aborts before any write, as today.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test agents --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/migrations/0007_seed_transcription_models.py backend/agents/models.py backend/agents/pricing.py backend/agents/tests
git commit -m "feat(agents): seed and validate supported transcription models"
```

---

### Task 5: Attempt records in `usage`

**Files:**
- Modify: `backend/usage/models.py` (`KIND_CHOICES`)
- Modify: `backend/usage/utils.py`
- Create: `backend/usage/migrations/0009_transcription_kind.py`
- Test: `backend/usage/tests/test_attempt_usage.py` (new)

**Interfaces:**
- Produces:
  - `start_attempt(project_id, model, *, seconds, conversation_id=None, message_id=None, attachment_index=0) -> str | None` — returns `str(UsageEvent.pk)`, `None` on any failure, never raises.
  - `finish_attempt(attempt_id, *, tokens: int, cost_usd: Decimal | None, status: str, usage: dict | None = None) -> None` — never raises.
- Attempt metadata: `{"status": "started"| "reported"| "failed", "seconds": int, "attachment_index": int, "provider_usage": {...}}`.

- [ ] **Step 1: Write the failing tests**

```python
class AttemptUsageTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-attempt")

	def test_start_records_unknown_cost(self):
		pk = start_attempt(str(self.project.id), "elevenlabs/scribe-v2", seconds=12)
		e = UsageEvent.objects.get(pk=pk)
		assert (e.kind, e.cost_usd, e.cost_source) == ("transcription", None, "unknown")
		assert e.metadata == {"status": "started", "seconds": 12, "attachment_index": 0}

	def test_finish_records_reported_cost_then_failure_keeps_null(self):
		pk = start_attempt(str(self.project.id), "m", seconds=12)
		finish_attempt(pk, tokens=9, cost_usd=Decimal("0.002"), status="reported", usage={"cost": 0.002})
		e = UsageEvent.objects.get(pk=pk)
		assert (e.tokens, e.cost_usd, e.cost_source) == (9, Decimal("0.002"), "provider_reported")
		assert e.metadata["status"] == "reported"

	def test_finish_without_cost_stays_unknown(self):
		pk = start_attempt(str(self.project.id), "m", seconds=12)
		finish_attempt(pk, tokens=0, cost_usd=None, status="failed")
		e = UsageEvent.objects.get(pk=pk)
		assert (e.cost_usd, e.cost_source) == (None, "unknown")

	def test_never_raises_on_bad_input(self):
		assert start_attempt("not-a-uuid", "m", seconds=1) is None
		finish_attempt("not-a-pk", tokens=0, cost_usd=None, status="failed")
		finish_attempt("00000000-0000-0000-0000-000000000000", tokens=0, cost_usd=None, status="failed")
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test usage.tests.test_attempt_usage --noinput`
Expected: FAIL — `ImportError: cannot import name 'start_attempt'`.

- [ ] **Step 3: Implement**

- `usage/models.py`: add `("transcription", "Transcription")` to `UsageEvent.KIND_CHOICES`.
- Migration `0009_transcription_kind.py`: `AlterField` on `kind` with the new choices (no data change).
- `usage/utils.py`: add both helpers. `start_attempt` creates a row with `tokens=0`, `cost_usd=None`, `cost_source="unknown"`, `provider="openrouter"`, `model_name=model`, `metadata` as above, wrapped in `try/except Exception: log; return None`. `finish_attempt` does a single `UsageEvent.objects.filter(pk=attempt_id).update(...)` setting `tokens`, `cost_usd`, `cost_source` (`provider_reported` when `cost_usd is not None`, else `unknown`) and merged `metadata`, wrapped the same way.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test usage --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/usage
git commit -m "feat(usage): record transcription attempts before and after the call"
```

---

### Task 6: The transcription task (success path)

**Files:**
- Modify: `backend/channels_app/media_store.py`
- Modify: `backend/channels_app/media.py` (feature gate)
- Modify: `backend/core/settings.py`
- Test: `backend/channels_app/tests/test_audio_transcription.py` (new)

**Interfaces:**
- Produces:
  - `transcription_on(project) -> bool` — `settings.TRANSCRIPTION_ENABLED` and (empty allowlist or `project.slug in settings.TRANSCRIPTION_PROJECTS`).
  - `wants_audio_reply(project, attachment_info) -> bool` — `transcription_on(project)` and any attachment with `type == "audio"`.
  - Settings: `TRANSCRIPTION_ENABLED` (bool, default `False`), `TRANSCRIPTION_PROJECTS` (list, default `[]`), `TRANSCRIPTION_MODEL` (str, default `""`), `TRANSCRIPTION_MAX_SECONDS` (int, default `300`).
  - `update_message(message_id, change) -> Message` — locks the row, calls `change(msg)` (mutates `meta`/`text`), saves both fields.
  - `transcribe_attachment(message_id: str, index: int) -> None` (celery task, `soft_time_limit=60, time_limit=90`)
  - `maybe_schedule_reply(message_id: str) -> None`
  - `TRANSCRIPTION_LOCK_SECONDS = 180`
  - `TERMINAL_STATES = {"transcribed", "failed", "failed_uncertain", "empty", "unmeasurable", "over_cap", "unaffordable"}`
  - `refund_transcription(project, message_id, index, reason) -> None` — looks up the charge row by key; no row → nothing; else `grant(project, -txn.amount, "adjust", key=f"transcription_refund:{message_id}:{index}", note=...)`; never raises. (Defined here because the error branch below needs it; Task 7 adds the remaining failure branches.)
  - state: `Message.meta["transcription"][str(index)] = {"status", "model", "seconds", "units", "attempt", "text"?}`
  - Route: `CELERY_TASK_ROUTES["channels_app.media_store.transcribe_attachment"] = {"queue": "replies"}`
- Consumes: Task 1 (`charge_if_affordable`, `InsufficientCredits`), Task 2 (`audio_format`, `audio_seconds`, `transcribe`, `resolve_transcription_model`), Task 5 (`start_attempt`, `finish_attempt`), existing `read_stored`, `schedule_reply`, `pending_turn`.

Patch targets in tests: `media_store` imports names directly (`from .reply import schedule_reply`), so patch `channels_app.media_store.schedule_reply`, `media_store.transcribe`, etc. — **not** `media_store.reply.schedule_reply`, which does not exist.

- [ ] **Step 1: Write the failing tests**

`backend/channels_app/tests/test_audio_transcription.py` — build a real `Message` with an audio attachment whose `path` points at a fixture stored through `default_storage`, then call the task synchronously (`.apply()` or by calling the function).

```python
class AudioGateTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-gate")

	def test_off_by_default(self):
		assert media.wants_audio_reply(self.project, [{"type": "audio"}]) is False

	@override_settings(TRANSCRIPTION_ENABLED=True)
	def test_on_for_every_project_when_the_allowlist_is_empty(self):
		assert media.wants_audio_reply(self.project, [{"type": "audio"}]) is True

	@override_settings(TRANSCRIPTION_ENABLED=True, TRANSCRIPTION_PROJECTS=["other-slug"])
	def test_allowlist_limits_the_pilot(self):
		assert media.wants_audio_reply(self.project, [{"type": "audio"}]) is False

	def test_photos_are_not_audio(self):
		with override_settings(TRANSCRIPTION_ENABLED=True):
			assert media.wants_audio_reply(self.project, [{"type": "image"}]) is False


class TranscribeSuccessTest(TestCase):
	def setUp(self):
		# project, knowledge base, contact, conversation (platform="whatsapp"), inbound Message with
		# attachment_info=[{"type": "audio", "path": <stored fixture key>}], status="pending"
		...

	def test_success_appends_text_and_charges_per_seconds(self):
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("hello there", 7, Decimal("0.001"), "elevenlabs/scribe-v2", {})), \
		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		seconds = self.msg.meta["transcription"]["0"]["seconds"]
		assert self.msg.text == "hello there"
		assert self.msg.meta["transcription"]["0"]["status"] == "transcribed"
		txn = CreditTransaction.objects.get(idempotency_key=f"transcription:{self.msg.id}:0")
		assert txn.amount == Decimal(-((seconds + 4) // 5))   # ceil(seconds / 5), 1 credit each
		assert service.balance_of(self.project) == txn.amount
		assert scheduled.called

	def test_price_is_applied_once(self):
		ActionPrice.objects.filter(action="transcription").update(credits=Decimal("2"))
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("hi", 3, None, "m", {})):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		units = self.msg.meta["transcription"]["0"]["units"]
		txn = CreditTransaction.objects.get(idempotency_key=f"transcription:{self.msg.id}:0")
		assert txn.amount == Decimal(-2 * units)   # not -4 * units

	def test_free_price_transcribes_without_any_rows(self):
		ActionPrice.objects.filter(action="transcription").update(credits=Decimal("0"))
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("hi", 3, None, "m", {})):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "transcribed"
		assert not CreditTransaction.objects.filter(action="transcription").exists()

	def test_bin_extension_is_transcribed_from_its_bytes(self):
		# Messenger/Instagram audio is stored as .bin; the format must come from the bytes
		key = default_storage.save("chat_media/p/x.bin", ContentFile((FIXTURES / "voice_messenger.mp3").read_bytes()))
		Message.objects.filter(id=self.msg.id).update(attachment_info=[{"type": "audio", "path": key}])
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("hi", 3, None, "m", {})) as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert call.call_args.args[1] == "mp3"

	def test_unstored_audio_fails_without_a_charge(self):
		Message.objects.filter(id=self.msg.id).update(attachment_info=[{"type": "audio", "url": "https://x/a.mp4"}])
		with mock.patch.object(media_store, "transcribe") as call, \
		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "failed"
		assert not CreditTransaction.objects.exists()
		assert scheduled.called

	def test_unexpected_error_ends_failed_refunded_and_replied(self):
		with mock.patch.object(media_store, "start_attempt", side_effect=RuntimeError("db down")), \
		     mock.patch.object(media_store, "transcribe") as call, \
		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "failed"
		assert service.balance_of(self.project) == Decimal("0.00")
		assert scheduled.called

	def test_task_is_routed_to_the_replies_queue(self):
		assert settings.CELERY_TASK_ROUTES["channels_app.media_store.transcribe_attachment"] == {"queue": "replies"}

	def test_typed_text_is_preserved(self):
		Message.objects.filter(id=self.msg.id).update(text="see this")
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("and this", 3, None, "m", {})):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		assert self.msg.text == "see this and this"

	def test_charge_happens_before_the_provider_call(self):
		order = []
		with mock.patch.object(media_store, "charge_if_affordable", side_effect=lambda *a, **k: order.append("charge")), \
		     mock.patch.object(media_store, "transcribe", side_effect=lambda *a, **k: order.append("call")):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert order == ["charge", "call"]

	def test_second_run_does_not_recharge_or_recall(self):
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("hi", 3, None, "m", {})) as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
			cache.delete(f"transcribe:{self.msg.id}:0")   # prove the state check stops it, not the claim
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert call.call_count == 1
		assert CreditTransaction.objects.filter(idempotency_key=f"transcription:{self.msg.id}:0").count() == 1

	def test_processing_from_a_dead_run_is_not_called_again(self):
		service.charge_if_affordable(self.project, "transcription", key=f"transcription:{self.msg.id}:0", units=3)
		media_store.update_message(str(self.msg.id), lambda m: m.meta.update(transcription={"0": {"status": "processing", "units": 3}}))
		with mock.patch.object(media_store, "transcribe") as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "failed_uncertain"
		assert service.balance_of(self.project) == Decimal("0.00")

	def test_two_attachments_charge_separately_and_join_in_order(self):
		att = list(self.msg.attachment_info) + [dict(self.msg.attachment_info[0])]
		Message.objects.filter(id=self.msg.id).update(attachment_info=att)
		texts = iter(["first", "second"])
		with mock.patch.object(media_store, "transcribe", side_effect=lambda *a, **k: Transcript(next(texts), 5, None, "m", {})), \
		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.transcribe_attachment(str(self.msg.id), 0)
			media_store.transcribe_attachment(str(self.msg.id), 1)
		self.msg.refresh_from_db()
		assert self.msg.text == "first second"
		assert CreditTransaction.objects.filter(action="transcription").count() == 2
		assert scheduled.call_count == 1
```

Also assert the reply is scheduled only once after both attachments finish. The test module imports `cache`, `settings`, `default_storage`, `ContentFile`, `ActionPrice`, and `FIXTURES = Path(agents.tests.__file__).parent / "fixtures"`.

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test channels_app.tests.test_audio_transcription --noinput`
Expected: FAIL — `AttributeError: module 'channels_app.media_store' has no attribute 'transcribe_attachment'`.

- [ ] **Step 3: Implement the gate, the helper and the task**

`backend/core/settings.py`: the four `TRANSCRIPTION_*` settings, `TRANSCRIPTION_ENABLED` defaulting to `False`; add `"channels_app.media_store.transcribe_attachment": {"queue": "replies"}` to `CELERY_TASK_ROUTES` (it routes by task name; without the entry the task runs on the ingestion queue).

`backend/channels_app/media.py`: `transcription_on(project)` and `wants_audio_reply(project, attachment_info)` as specified in Interfaces. Import `settings` if the module does not already.

`backend/channels_app/media_store.py`:

- `update_message`: `with transaction.atomic(): msg = Message.objects.select_for_update().get(id=message_id); change(msg); msg.save(update_fields=["meta", "text"]); return msg`.
- `transcribe_attachment` (a `_set_state(message_id, index, status, **fields)` helper over `update_message` keeps each step one line):
  1. `if not cache.add(f"transcribe:{message_id}:{index}", 1, timeout=TRANSCRIPTION_LOCK_SECONDS): return`
  2. Load the message with `select_related("project", "conversation")`; read `attachment_info[index]`; return unless `type == "audio"`.
  3. Read the state via `update_message` (no change). **Status in `TERMINAL_STATES` → return** (no charge, no call). **`processing` → `failed_uncertain`, `refund_transcription(..., "interrupted")`, return** (at-most-once: a dead run is never called again).
  4. Step 3 and everything below run inside `try: … except Exception as e: logger.warning(f"Transcription of message {message_id} attachment {index} failed: {type(e).__name__}"); _set_state(..., "failed"); refund_transcription(..., "error")` and `finally: maybe_schedule_reply(message_id)`. No exception from our own code can leave the attachment non-terminal.
  5. No `path` (storage failed) → `failed`, return (no charge yet).
  6. `data = read_stored(path, AUDIO_MAX_BYTES)`; `fmt = audio_format(data)`; `seconds = audio_seconds(data, fmt) if fmt else None`. `None` → `unmeasurable`; over `settings.TRANSCRIPTION_MAX_SECONDS` → `over_cap`. Both return with no charge.
  7. `units = max(1, math.ceil(seconds / PricingSettings.get().transcription_seconds_per_credit))`. **Do not multiply by the price.**
  8. `try: charge_if_affordable(project, "transcription", key=f"transcription:{message_id}:{index}", units=units, conversation=msg.conversation, trigger_message=msg)` `except InsufficientCredits: _set_state(..., "unaffordable"); return`. A `None` return (free) carries on.
  9. `model = resolve_transcription_model()`; `_set_state(..., "processing", model=model, seconds=seconds, units=units)`; `attempt = start_attempt(...)`.
  10. `try: out = transcribe(data, fmt, model)` `except TranscriptionError: finish_attempt(attempt, status="failed", ...); _set_state(..., "failed"); refund_transcription(..., "provider error"); return` (Task 7 tests this branch).
  11. `finish_attempt(attempt, status="reported", tokens=out.tokens, cost_usd=out.cost_usd, usage=out.usage)`. Empty `out.text.strip()` → `empty` + refund (Task 7). Otherwise one `update_message` that appends `out.text` to `msg.text` and sets state `transcribed` with `attempt`.
- `maybe_schedule_reply`: load the message; every audio attachment (stored or not) must have a state in `TERMINAL_STATES`. If so, and `cache.add(f"transcribe-done:{message_id}", 1, timeout=3600)`, call `schedule_reply(turn[-1])` where `turn = pending_turn(msg.conversation)` — the **newest** pending inbound message, not `msg`. (Scheduling `msg` would skip as "batched" behind a text that arrived during transcription, and nobody would answer until recovery.) Empty turn → nothing to do.
- `store_message_media`: replace the `finally` block's unconditional `schedule_reply` with: when `reply` and the message has any audio attachment and `transcription_on(project)`, enqueue one `transcribe_attachment.delay(str(msg.id), i)` per audio attachment, **stored or not** (an unstored one fails in the task, which then drives the canned reply); otherwise `schedule_reply(msg)` as today. Keep the `finally` so a storage failure still schedules something.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test channels_app.tests.test_audio_transcription --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/media_store.py backend/channels_app/media.py backend/core/settings.py backend/channels_app/tests/test_audio_transcription.py
git commit -m "feat(channels): transcribe each voice note and charge before the call"
```

---

### Task 7: Failure paths, refunds and the sweep

**Files:**
- Modify: `backend/channels_app/media_store.py`
- Create: `backend/channels_app/tests/test_transcription_failures.py`
- Modify: `backend/core/settings.py` (beat entry)

**Interfaces:**
- Produces: `reconcile_transcriptions() -> None` (celery task).
- Consumes: `refund_transcription` and `TERMINAL_STATES` from Task 6. This task adds tests for each failure branch Task 6 wired, plus the sweep.

- [ ] **Step 1: Write the failing tests**

```python
class TranscriptionFailureTest(TestCase):
	def test_empty_transcript_is_not_billed(self):
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("   ", 0, Decimal("0.001"), "m", {})):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "empty"
		assert service.balance_of(self.project) == Decimal("0.00")
		assert CreditTransaction.objects.filter(kind="adjust").count() == 1

	def test_provider_error_is_refunded_and_attempt_recorded(self):
		with mock.patch.object(media_store, "transcribe", side_effect=TranscriptionError("500")):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "failed"
		assert service.balance_of(self.project) == Decimal("0.00")
		attempt = UsageEvent.objects.get(kind="transcription")
		assert (attempt.cost_usd, attempt.metadata["status"]) == (None, "failed")

	def test_refund_without_a_charge_row_grants_nothing(self):
		media_store.refund_transcription(self.project, str(self.msg.id), 0, "test")
		assert not CreditTransaction.objects.exists()

	def test_unmeasurable_audio_never_calls_the_provider(self):
		with mock.patch.object(media_store, "audio_seconds", return_value=None), \
		     mock.patch.object(media_store, "transcribe") as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		assert service.balance_of(self.project) == Decimal("0.00")

	def test_over_cap_never_calls_the_provider(self):
		with override_settings(TRANSCRIPTION_MAX_SECONDS=1), \
		     mock.patch.object(media_store, "transcribe") as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()

	def test_unaffordable_project_never_calls_the_provider(self):
		with mock.patch.object(media_store, "charge_if_affordable", side_effect=InsufficientCredits(Decimal("3"), Decimal("-50"))), \
		     mock.patch.object(media_store, "transcribe") as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "unaffordable"

	def test_a_refunded_failure_is_never_called_again(self):
		with mock.patch.object(media_store, "transcribe", side_effect=TranscriptionError("500")) as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
			cache.delete(f"transcribe:{self.msg.id}:0")
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert call.call_count == 1   # a second success here would be a free transcription
		assert CreditTransaction.objects.filter(kind="adjust").count() == 1
		assert service.balance_of(self.project) == Decimal("0.00")

	def test_sweep_refunds_a_charge_whose_call_never_returned(self):
		service.charge_if_affordable(self.project, "transcription", key=f"transcription:{self.msg.id}:0", units=3)
		media_store.update_message(str(self.msg.id), lambda m: m.meta.update(
			transcription={"0": {"status": "processing", "units": 3, "seconds": 12}}))
		Message.objects.filter(id=self.msg.id).update(created_at=timezone.now() - timedelta(minutes=20))
		media_store.reconcile_transcriptions()
		self.msg.refresh_from_db()
		assert service.balance_of(self.project) == Decimal("0.00")
		assert self.msg.meta["transcription"]["0"]["status"] == "failed_uncertain"

	def test_sweep_ignores_messages_older_than_two_hours(self):
		service.charge_if_affordable(self.project, "transcription", key=f"transcription:{self.msg.id}:0", units=3)
		media_store.update_message(str(self.msg.id), lambda m: m.meta.update(transcription={"0": {"status": "processing", "units": 3}}))
		Message.objects.filter(id=self.msg.id).update(created_at=timezone.now() - timedelta(hours=3))
		media_store.reconcile_transcriptions()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "processing"

	def test_sweep_leaves_a_transcribed_attachment_alone(self):
		self.msg.meta = {"transcription": {"0": {"status": "transcribed", "units": 3}}}
		self.msg.save(update_fields=["meta"])
		Message.objects.filter(id=self.msg.id).update(created_at=timezone.now() - timedelta(minutes=20))
		with mock.patch.object(media_store, "refund_transcription") as refund:
			media_store.reconcile_transcriptions()
		refund.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "transcribed"
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test channels_app.tests.test_transcription_failures --noinput`
Expected: FAIL — `AttributeError: ... has no attribute 'reconcile_transcriptions'`. The branch tests may already pass from Task 6; any that fail point at a Task 6 bug.

- [ ] **Step 3: Implement**

- The failure branches and `refund_transcription` already exist from Task 6; fix whatever these tests expose there. Unmeasurable and over-cap happen before the charge, so they refund nothing (and `refund_transcription` would find no row anyway).
- `reconcile_transcriptions`: select up to 100 messages with `meta__has_key="transcription"` and `created_at__range=(now - 2 h, now - 10 min)`, oldest first. The lower bound is required: without it, old finished rows fill every batch once 100 voice notes exist, and new stranded charges are never refunded. For each state entry with `status == "processing"`, `refund_transcription(..., "interrupted")` and set `failed_uncertain`; then call `maybe_schedule_reply`. Never raise out of the task.
- `core/settings.py` `CELERY_BEAT_SCHEDULE`: add `'reconcile-transcriptions': {'task': 'channels_app.media_store.reconcile_transcriptions', 'schedule': 600}`.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test channels_app.tests.test_transcription_failures --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/media_store.py backend/channels_app/tests/test_transcription_failures.py backend/core/settings.py
git commit -m "feat(channels): refund failed transcriptions and sweep stranded charges"
```

---

### Task 8: Reply pipeline — recovery guard, failure reply, mixed content

**Files:**
- Modify: `backend/channels_app/reply.py`
- Modify: `backend/channels_app/tasks.py` (`format_message_with_context`)
- Modify: `backend/core/settings.py` (message strings)
- Test: `backend/channels_app/tests/test_reply_pipeline.py`

**Interfaces:**
- Produces, all in `reply.py`, all reading `meta["transcription"]` states (there is **no** `transcription_failed` flag):
  - `_audio_states(turn) -> list[str | None]` — the state of every audio attachment in the turn (`None` when not yet written).
  - `_has_untranscribed_audio(turn) -> bool` — any state `None` or `processing`.
  - `_audio_unaffordable(turn) -> bool` — no message in the turn has text, and any state is `unaffordable`.
  - `_audio_failed(turn) -> bool` — no message in the turn has text, there is audio, and no state is `transcribed`.
  - `_recent_bengali(conversation) -> bool`.
- Consumes: `media_store.transcribe_attachment` (imported inside the function to avoid a cycle; `media_store` already imports `reply`), `media_store.TERMINAL_STATES`, `settings.TRANSCRIPTION_FAILED_MESSAGE(_BN)`.
- No `_save_and_deliver` extraction: the canned reply goes through `_reply`'s existing tail, the same way the photo-timeout fallback does.

- [ ] **Step 1: Write the failing tests**

In `backend/channels_app/tests/test_reply_pipeline.py`, reuse the existing `PipelineBase` (it already stubs `channels_app.tasks.generate_reply`, the sender, and `deliver_outbound.delay`):

```python
class AudioTurnTest(PipelineBase):
	def audio_message(self, text="", meta=None):
		return Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in",
			text=text, has_attachments=True,
			attachment_info=[{"type": "audio", "path": "chat_media/p/a.ogg"}],
			meta={"audio_reply": True, **(meta or {})}, status="pending",
		)

	FAILED = {"transcription": {"0": {"status": "failed"}}}
	UNAFFORDABLE = {"transcription": {"0": {"status": "unaffordable"}}}

	def test_newer_voice_note_gets_its_own_reply_task(self):
		older = self.audio_message(meta=self.FAILED)
		self.audio_message()
		assert reply._newer_text_message(self.conv, older.created_at) is True

	def test_text_during_transcription_is_answered_when_it_ends(self):
		voice = self.audio_message()
		text = Message.objects.create(project=self.project, contact=self.contact, conversation=self.conv,
			direction="in", text="price?", status="pending")
		with mock.patch("channels_app.media_store.transcribe_attachment"):
			process_reply.apply(args=[str(self.conv.id), str(text.id)])   # sees untranscribed audio, waits
		assert not Message.objects.filter(direction="out").exists()
		media_store.update_message(str(voice.id), lambda m: (m.meta.update(transcription={"0": {"status": "transcribed"}}), setattr(m, "text", "do you have red")))
		with mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.maybe_schedule_reply(str(voice.id))
		assert scheduled.call_args.args[0].id == text.id   # the newest pending message, not the voice note

	def test_a_turn_with_untranscribed_audio_queues_transcription_instead_of_replying(self):
		msg = self.audio_message()
		with mock.patch("channels_app.media_store.transcribe_attachment") as enqueued:
			process_reply.apply(args=[str(self.conv.id), str(msg.id)])
		assert enqueued.delay.called
		assert not Message.objects.filter(direction="out").exists()
		msg.refresh_from_db()
		assert msg.status == "pending"      # stays recoverable, not completed

	def test_failed_voice_note_sends_canned_reply_without_the_agent(self):
		msg = self.audio_message(meta=self.FAILED)
		process_reply.apply(args=[str(self.conv.id), str(msg.id)])
		self.gen.assert_not_called()
		assert Message.objects.filter(direction="out").last().text == settings.TRANSCRIPTION_FAILED_MESSAGE
		assert CreditTransaction.objects.filter(action="reply").count() == 0

	def test_unaffordable_voice_note_hands_off(self):
		msg = self.audio_message(meta=self.UNAFFORDABLE)
		process_reply.apply(args=[str(self.conv.id), str(msg.id)])
		self.gen.assert_not_called()
		assert Message.objects.filter(direction="out").last().text == settings.HANDOFF_MESSAGE
		self.conv.refresh_from_db()
		assert self.conv.handoff_reason == "out of credits"

	def test_failed_audio_with_typed_text_still_answers_the_text(self):
		msg = self.audio_message(text="how much is this", meta=self.FAILED)
		process_reply.apply(args=[str(self.conv.id), str(msg.id)])
		assert self.gen.called
		assert Message.objects.filter(direction="out").last().text == self.reply_text

	def test_transcript_suppresses_the_generic_attachment_note(self):
		msg = self.audio_message()
		msg.text = "hello"
		msg.meta["transcription"] = {"0": {"status": "transcribed"}}
		msg.save(update_fields=["text", "meta"])
		assert "Message includes attachments" not in tasks.format_message_with_context(msg)
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test channels_app.tests.test_reply_pipeline channels_app.tests.test_audio_transcription --noinput`
Expected: FAIL — `_newer_text_message` False for audio; the agent is called for failed and unaffordable notes; the note still appears.

- [ ] **Step 3: Implement**

- `_newer_text_message`: add `| Q(meta__audio_reply=True)` to the existing filter.
- Add the guard at the top of `_reply`, after the paused check: when `_has_untranscribed_audio(turn)`, enqueue `transcribe_attachment.delay(str(m.id), index)` for each untranscribed attachment and return **without completing** the inbound rows (they must stay `pending` for recovery). A task still running holds its claim, so the enqueue is a no-op for it.
- Unaffordable: right after the existing `out_of_credits` computation, `out_of_credits = out_of_credits or _audio_unaffordable(turn)`. The existing handoff path does the rest.
- Failure: compute `audio_failed = not out_of_credits and _audio_failed(turn)` next to `wants_person`, add it to `skip_vision`, and add a branch to the existing `if out_of_credits / elif wants_person / elif photos_timed_out` chain: `elif audio_failed: reply_text = settings.TRANSCRIPTION_FAILED_MESSAGE_BN if _recent_bengali(conversation) else settings.TRANSCRIPTION_FAILED_MESSAGE`. `agent_replied` stays `False`, so the existing tail saves and delivers it with no `reply` charge.
- `_recent_bengali`: last 10 inbound messages of the conversation, any Bengali codepoint in `text` (same range as the existing check in `_reply`).
- `format_message_with_context`: skip the generic attachment note when any `meta["transcription"]` entry has `status == "transcribed"`.
- Settings: add `TRANSCRIPTION_FAILED_MESSAGE` and `TRANSCRIPTION_FAILED_MESSAGE_BN` with the copy from the spec.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test channels_app.tests.test_reply_pipeline channels_app.tests.test_audio_transcription --noinput`
Expected: PASS, and the pre-existing reply-pipeline tests still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/reply.py backend/channels_app/tasks.py backend/core/settings.py backend/channels_app/tests
git commit -m "feat(channels): answer voice notes, with a deterministic failure reply"
```

---

### Task 9: Webhooks — let voice notes ask for a reply

**Files:**
- Modify: `backend/channels_app/service/whatsapp_messaging.py:59-90`
- Modify: `backend/channels_app/service/facebook_messing.py:178-200`
- Test: `backend/channels_app/tests/test_media_store.py`, `backend/channels_app/tests/test_facebook_webhook.py`

**Interfaces:**
- Consumes: `media.wants_audio_reply(project, attachment_info)` (Task 6), the four `TRANSCRIPTION_*` settings (Task 6).

- [ ] **Step 1: Write the failing tests**

In `backend/channels_app/tests/test_media_store.py`, with `@override_settings(TRANSCRIPTION_ENABLED=True)`:
- a WhatsApp audio message is created `status="pending"` with `meta["audio_reply"] is True` and queues exactly one `store_message_media`;
- with the flag off (the default), the same payload creates a `completed` message and schedules nothing (today's behaviour).

In `backend/channels_app/tests/test_facebook_webhook.py`, with the flag on:
- a Messenger audio attachment gets `audio_reply`;
- an Instagram audio attachment, delivered through the shared handler, gets `audio_reply` too.

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose exec django python manage.py test channels_app.tests.test_media_store channels_app.tests.test_facebook_webhook --noinput`
Expected: FAIL — audio messages stay `completed` and no `audio_reply` key is written.

- [ ] **Step 3: Implement**

- `whatsapp_messaging.py`: `audio_reply = wants_audio_reply(cred.project, attachments)`; `wants_reply = bool(text) or photo_reply or audio_reply`; add `**({"audio_reply": True} if audio_reply else {})` to `meta` beside the existing `photo_reply` spread.
- `facebook_messing.py`: the same two edits against its `wants_reply` and `meta`.

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose exec django python manage.py test channels_app --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/media.py backend/channels_app/service backend/core/settings.py backend/channels_app/tests
git commit -m "feat(channels): let voice notes request a reply behind the transcription gate"
```

---

### Task 10: Customer-facing credits, pricing copy, docs

**Files:**
- Modify: `backend/channels_app/api.py:250`
- Modify: `frontend/lib/billing-api.ts:17`
- Modify: `frontend/components/billing/PricingSection.tsx:17-25`
- Test: `backend/channels_app/tests/test_conversation_api.py`

**Interfaces:**
- Consumes: `per_seconds` from Task 3.

- [ ] **Step 1: Write the failing tests**

In `backend/channels_app/tests/test_conversation_api.py`, extend an existing conversation-messages test class:

```python
	def test_transcription_charge_shows_on_the_customer_message(self):
		service.grant(self.project, Decimal("10"), "adjust", key="seed")
		service.charge(self.project, "transcription", key=f"transcription:{self.msg.id}:0", units=2,
		               conversation=self.conv, trigger_message=self.msg)
		res = self.client.get(f"/api/channels/conversations/{self.conv.id}/")   # match the URL the file already uses
		entry = next(m for m in res.json()["data"]["messages"] if m["id"] == str(self.msg.id))["credits"][0]
		assert entry == {"action": "transcription", "label": "Voice note transcription", "credits": "2.00"}

	def test_refunded_transcription_charge_is_hidden(self):
		service.grant(self.project, Decimal("10"), "adjust", key="seed")
		txn = service.charge(self.project, "transcription", key=f"transcription:{self.msg.id}:0", units=2,
		                     conversation=self.conv, trigger_message=self.msg)
		service.grant(self.project, -txn.amount, "adjust", key=f"transcription_refund:{self.msg.id}:0")
		res = self.client.get(f"/api/channels/conversations/{self.conv.id}/")   # match the URL the file already uses
		entry = next(m for m in res.json()["data"]["messages"] if m["id"] == str(self.msg.id))
		assert not any(c["action"] == "transcription" for c in entry.get("credits") or [])
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose exec django python manage.py test channels_app.tests.test_conversation_api --noinput`
Expected: FAIL — the transcription entry is absent (the `action__in` filter excludes it).

- [ ] **Step 3: Implement**

- `channels_app/api.py` `_message_credits`: `action__in=("reply", "photo_search", "transcription")`; the existing branch already attaches non-reply charges to `trigger_message_id`. Then skip refunded transcription charges with one extra query:

```python
	rows = list(rows.order_by("created_at"))
	refunded = set(CreditTransaction.objects.filter(idempotency_key__in=[
		t.idempotency_key.replace("transcription:", "transcription_refund:", 1) for t in rows if t.action == "transcription"
	]).values_list("idempotency_key", flat=True))
	# in the loop: skip t when t.action == "transcription" and its refund key is in `refunded`
```
- `frontend/lib/billing-api.ts`: `actions: { action: string; label: string; credits: string; per_seconds?: number }[]`.
- `PricingSection.tsx`: add
  `transcription: (credits) => \`Each voice note costs ${formatCredits(credits)} ${credits === 1 ? "credit" : "credits"} for each started 5 seconds of audio, minimum one. Nothing is charged if we cannot transcribe it.\``
  and, where the table renders a row, append ` per ${row.per_seconds} seconds` when `per_seconds` is present.
- Update the spec's status line to `approved, implemented` only after the whole plan passes.

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose exec django python manage.py test billing channels_app --noinput`
Then, in `frontend/`: `npm run build` (or `npx tsc --noEmit`) to prove the type still compiles.

- [ ] **Step 5: Commit**

```bash
git add backend/channels_app/api.py backend/channels_app/tests/test_conversation_api.py frontend/lib/billing-api.ts frontend/components/billing/PricingSection.tsx
git commit -m "feat(billing): show voice note charges and their per-seconds price"
```

---

## Final verification

- [ ] `docker compose exec django python manage.py test billing agents usage channels_app --noinput` — all green.
- [ ] `docker compose exec django python manage.py makemigrations --check --dry-run` — no pending model changes.
- [ ] `grep -rn "TRANSCRIPTION_ENABLED" backend/core/settings.py` — the default is still `False`.
- [ ] `grep -rn "transcribe\|transcript" backend --include=*.py | grep -i "logger\."` — no log line carries transcript text.
- [ ] `grep -n "transcribe_attachment" backend/core/settings.py` — routed to `replies`.
- [ ] `grep -rn "price_of(\"transcription\")" backend/channels_app` — no hits: the caller never multiplies by the price.
- [ ] `git ls-files backend/agents/tests/fixtures` — only the Task 0 recordings and captures; nothing copied from `chat_media/`.
- [ ] Commit the plan's completion with a `docs:` commit touching the spec's status line.
