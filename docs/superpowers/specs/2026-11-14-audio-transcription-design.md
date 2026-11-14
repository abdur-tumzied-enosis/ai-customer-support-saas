# Audio transcription — design (rev 4)

Size: project. Status: draft, awaiting approval. Rev 4 answers the code-level review of 2026-11-14 (rev 3
answered the second design review).

Every voice note a customer sends is transcribed to text, the text is answered like any other
message, and a successful transcription is billed by the started 5 seconds of audio.

## Revision 3 in one line

**Charge before the provider call, refund on failure.** That single inversion closes the
concurrency race exactly, removes the "transcribed but never billed" window entirely, and needs one
new function in `billing.service` instead of a reservation field or a new ledger state.

## Changes from revision 3

Each row was checked against the code on 2026-11-14.

| # | Change | Why |
|---|---|---|
| 1 | `charge_if_affordable` looks up the idempotency key **before** the affordability check | a retry after a successful charge on a now-low wallet was refused, marked `unaffordable`, and never refunded |
| 2 | It raises the existing `InsufficientCredits` when unaffordable; `None` means a zero-priced action, as with `charge()` | `None` meant both, so a free price made every voice note "unaffordable" |
| 3 | `units = ceil(seconds / seconds_per_credit)`; the price is applied once, inside the charge; refunds are `-txn.amount` read from the charge row | the price was multiplied twice, and a refund could be granted with no charge behind it |
| 4 | The audio format comes from the file's bytes, not its extension; tinytag gets `file_obj=` plus a filename hint | Messenger audio URLs end in `.mp4`, which `_audio_ext` does not map, and Instagram URLs have no extension, so both are stored as `.bin`; tinytag's own sniffing only knows the `M4A` MP4 brand |
| 5 | The failure and unaffordable branches read the per-attachment states | the plan keyed on a `meta["transcription_failed"]` flag nothing wrote |
| 6 | Every terminal state is skipped; `processing` becomes `failed_uncertain` with no second call | only `transcribed` was skipped, so a refunded failure could be called again and transcribed for free |
| 7 | `transcribe_attachment` is routed to the `replies` queue | `CELERY_TASK_ROUTES` routes by task name, so it would have run on the ingestion queue |
| 8 | `maybe_schedule_reply` schedules the newest pending inbound message, not the audio message | a text sent just after a voice note waited 5 minutes for `recover_stuck_replies` |
| 9 | An audio-only turn whose notes were all unaffordable hands off, like any out-of-credits turn | "we could not understand your voice note" was untrue there |
| 10 | The sweep only looks at the last 2 hours | `[:100]` with no lower bound filled up with old finished rows |
| 11 | Any unexpected error in the task ends in `failed` + refund; an audio attachment that could not be stored goes through the same task and fails there | a DB/R2 error left no terminal state and the note was dropped after an hour; a storage failure sent the agent an empty turn and billed a reply |
| 12 | `_message_credits` hides a transcription charge that was refunded | the conversation view showed a charge for a note that cost nothing |
| 13 | Gemini audio priced at 32 tokens/second: ~9× coverage, not 20× | the 1 token/second assumption was wrong |
| 14 | Fixtures are our own voice notes sent through the dev channels, never files from `chat_media/`; real provider responses are captured before any code (plan Task 0) | customer audio must not go into git; the response shape was guessed |
| 15 | The row lock's claim is narrowed: it protects transcription writers from each other; `image_reply` does not take it | `image_reply.py:138` and `:400` write `meta` unlocked; they run only after transcription is terminal, so they do not overlap |

## Changes from revision 2

| # | Change | Review item |
|---|---|---|
| 1 | `billing.service.charge_if_affordable()` — affordability check and charge in one locked transaction | P0 concurrency race (my rev-2 claim was wrong, see below) |
| 2 | Charge before the provider call; refund via an existing `grant(kind="adjust")` on failure or unusable text | P0 concurrency + P0 unbilled success, both closed by construction |
| 3 | A `UsageEvent` is written **before** the HTTP call with an unknown outcome, and finalised after it | P0 unrecorded killed attempt |
| 4 | One task per audio attachment plus a message-level completion check | P1 one task cannot bound N attachments |
| 5 | `update_message_meta()` helper: `select_for_update` + read-modify-write inside a transaction | P0 non-atomic `Message.meta` |
| 6 | Typed text is preserved and the transcript appended; partial success defined per attachment | P0 text overwrite, P1 mixed content |
| 7 | `TRANSCRIPTION_PROJECTS` allowlist in settings for the pilot | P1 global flag vs one-project pilot |
| 8 | `SUPPORTED_TRANSCRIPTION_MODELS` enforced in `ModelConfig.clean()` and at resolve time | P1 help text is not enforcement |
| 9 | Gemini cost arithmetic corrected: $0.003 per 60 s at the stated assumption (~20×), not 7–15× | P1 arithmetic |
| 10 | Local duration decides validation, affordability and the customer charge; provider duration, when captured, is stored for reconciliation only | P1 duration split |
| 11 | Input-format matrix tested per model | P1 format support is not provider support |

Retained from rev 2 on the reviewer's agreement: fixed per-5-seconds pricing, minimum 1 credit, no
charge for unusable text, local measurement before the call, transcription off by default, provider
cost separate from credits, deterministic failure reply, no new operation table, no new immutable
ledger.

## Correction to revision 2

Rev 2 said the concurrent-voice-note race was "bounded by `overdraft_credits`". That was wrong.
`shopper_blocked` reads the balance before any charge is posted, so N workers each see the same
balance and each pass:

| | credits |
|---|---|
| starting balance | 20 |
| overdraft floor | −50 |
| note A (60) passes: 20 − 60 = −40 ≥ −50 | yes |
| note B (60) passes on the same stale balance | yes |
| balance after both | −100 |

Prod runs `celery-replies` at `REPLY_WORKER_CONCURRENCY` (default 16 in `docker-compose.yml`), so N > 1 happens. Exposure
grows with N and is not bounded. Rev 3 closes it rather than documenting it.

## Current implementation: how we charge today

**Who holds the balance.** `billing.CreditWallet` — one per project, `balance` in credits.
`billing.CreditTransaction` is the append-only ledger: charges negative, grants positive. `save()`
refuses non-inserts and `delete()` raises, so a written row is permanent.

**What a credit is worth.** `billing.PricingSettings` is a singleton (`pk=1`):

| field | default | meaning |
|---|---|---|
| `usd_per_credit` | `0.005` | internal value of a credit when comparing against provider cost |
| `target_coverage_ratio` | `5` | charged value must be ≥ 5× known provider cost |
| `overdraft_credits` | `50` | how far a project may go negative before replies stop |
| `low_balance_credits` | `100` | low-balance warning threshold |
| `signup_credits` | `100` | one-off bonus |

**What each action costs.** `billing.models.ACTIONS` — `{action: (label, default credits)}` — seeded
into one `ActionPrice` row per action by migration `0002_seed`. Today: `reply` 1, `photo_search` 1,
`product_import` 2, `order_setup` 5, `photo_index` 0, `instruction_improve` 0, `product_suggest` 0.
`price_of(action)` reads `ActionPrice`, falling back to `ACTIONS` when the row is missing.

**How a charge is made.** Everything goes through `billing.service`:

- `charge(project, action, *, key, units=1, conversation, trigger_message, result_message)` — locks
  the wallet `FOR UPDATE`, writes one negative transaction of `price × units`, moves the balance,
  fires the "wallet depleted" email if it crossed zero. `key` is a unique idempotency key. `units`
  is a whole number. **It does not check affordability** — callers check first with
  `shopper_blocked`, which is where rev 2's race came from.
- `charge_affordable(project, action, rows, create)` — bulk path (catalog import), the only place
  check and charge already share a lock.
- `grant(project, amount, kind, key=...)` — positive rows.
- `charge_delivered_reply(out_msg)` — the reply charge, only if the outbound row carries
  `meta["billable"] == "reply"` and only after the platform accepted it. Key `reply:<out msg id>`.
  Never raises; `charge_missed_replies` reconciles.
- `photo_search` is charged in `channels_app.image_reply` when the search ran, keyed
  `photo_search:<msg id>`, any channel except `self`, never raises.

**The pre-turn affordability check.** `reply._reply` sums `price_of("reply") +
price_of("photo_search")` and calls `shopper_blocked(project, cost)`. A billing error fails open.

**Cost side, separate from credits.** `usage.UsageEvent` — one row per provider call: `kind`,
`tokens`, `cost_usd`, `cost_source` (`provider_reported` / `token_calculated` / `unknown`), a price
snapshot, optional `conversation` / `message` links, and free-form `metadata`. Rows are updatable
(only `CreditTransaction` is immutable), which is what makes the attempt-marker pattern possible.
Reported cost wins; unknown cost is `None`, never `0`.

**The loop that closes the two sides.** `billing.report.coverage(start, end)` compares
`credits × usd_per_credit` against summed known `cost_usd` per project, flags projects under
`target_coverage_ratio`, breaks charged credits down by action, and counts `unpriced_events`.

**What the customer sees.** `/api/billing/pricing` lists each `ACTIONS` entry with its live price;
`PricingSection.tsx` renders the table and a plain-language line per action. Per-message credits come
from `channels_app.api._message_credits`, which today covers `reply` and `photo_search`.

**What happens to a voice note today.** `media_store.store_message_media` downloads the audio, stores
it in R2 under `chat_media/<project>/` (cap 16 MB), then schedules the reply if the webhook asked
for one. Both webhook handlers set `wants_reply` to text-or-photo only, so **audio is stored and
never answered**. `ModelConfig.transcription_model` is seeded and read by nothing.

**Delivery recovery.** Celery does not use `acks_late`, so a killed worker does not redeliver.
`reply.recover_stuck_replies` (every 300 s) re-enqueues `process_reply` for inbound rows still
`pending` after 5 minutes; `recover_pending_deliveries` (every 30 s) re-sends outbound rows. Both are
claim-guarded.

## Intent

A customer who sends a voice note gets the same answer as one who types. We pay a provider per
second of audio, so the customer is charged per started 5 seconds of audio, for transcriptions that
produced usable text.

## Goals

- Transcribe every audio attachment on WhatsApp, Messenger and Instagram messages.
- Write the transcript into `Message.text` without destroying typed text.
- Charge `price_of("transcription") × ceil(measured_seconds / seconds_per_credit)` credits, minimum
  one unit, only on a usable transcript, and never let a concurrent turn spend past the overdraft.
- Record the provider's cost on every attempt, including attempts killed before a response.
- Let an admin pick `elevenlabs/scribe-v2` (primary), `x-ai/grok-stt-2.0` or
  `google/gemini-3.5-transcribe` in the existing `ModelConfig.transcription_model` slot.

## Non-goals

- Widget audio uploads. `media.presign_chat_photo` only accepts `image/jpeg|png|webp`, so the widget
  has no audio path. If one is added, it uses this same task.
- Staff voice notes, outbound audio, playback UI.
- Language hints, diarization, keyword prompting — auto-detect only.
- Deleting the three ASR rows already in the catalogue.
- Redoing the `usage` / `billing` split.

## Anti-goals

- Never bill for a transcription that produced no usable text (a charge that is refunded in the same
  transaction is not a bill; see "Failure is refunded").
- Never call the provider for audio we cannot measure or cannot bound in cost.
- Never leave a voice note with neither a reply nor a recorded failure.
- Billing must never break the reply, and a reply must never break billing.
- The transcript is customer content, never instructions.
- The transcript, the audio bytes and the audio URL never appear in logs.

## Constraints

- All three models are reached through OpenRouter:
  `POST https://openrouter.ai/api/v1/audio/transcriptions` with
  `{"model", "input_audio": {"data": "<base64>", "format": "<mp3|wav|ogg|m4a|...>"}}`.
- Catalogue prices, 2026-11-14: `elevenlabs/scribe-v2` ≈ $0.000061/s ($0.22/h);
  `x-ai/grok-stt-2.0` $0.000028/s; `google/gemini-3.5-transcribe` $2/M input tokens, $12/M output
  tokens. Sizing only — every recorded figure comes from `usage.cost`.
- Captured 2026-11-14 for each model from our own WhatsApp-style Ogg sample and Messenger-style MP3
  sample (all six calls HTTP 200):
  - `usage.cost` is present on all three models, and the values match the catalogue rates (Gemini:
    83 input + 6 output tokens = $0.000238, which is exactly $2/M input + $12/M output).
  - Token fields differ by model: `scribe-v2` and `grok-stt-2.0` return **no token count**; Gemini
    returns `input_tokens`, `output_tokens`, `total_tokens`. `tokens` is therefore the first of
    `input_tokens`, `prompt_tokens`, `total_tokens` that exists, else 0. Tokens feed only the usage
    record, never a charge.
  - `scribe-v2` and `grok-stt-2.0` also return `seconds`, the provider's own duration. It is kept in
    `usage` for reconciliation only; local measurement still decides the charge.
  - Format: all three accepted Ogg and MP3. No MP4/M4A sample is recorded yet, so the M4A/MP4 row of
    the format matrix is open (see Tests).
- Local duration: `tinytag` (MIT, pure Python), covering Ogg/Opus, MP4/M4A, MP3 and WAV. AMR is not in
  its list and is rejected as unmeasurable. Verified in the container on 2026-11-14: `tinytag==2.1.1`
  exists, `TinyTag.get(filename=None, file_obj=None, ...)` takes the bytes as `file_obj=`, a
  `filename` hint picks the parser by extension, and AMR raises `UnsupportedFormatError`. Its own
  magic-byte sniffing recognises MP4 only for the `M4A`/`M4B` brands, so we sniff the container
  ourselves and pass the hint.
- The stored file's extension cannot be trusted: Messenger audio URLs end in `.mp4`, which
  `media_store._audio_ext` does not map, and Instagram CDN URLs have no extension, so both are stored
  as `.bin`. The format is read from the bytes.
- A format tinytag can read is not automatically accepted by a model; the format matrix in Tests is
  tested per model before that model is enabled.
- `agents.pricing.sync_openrouter_prices` must also read `?output_modalities=transcription`, or ASR
  rows never get prices.
- `requests==2.32.5`, `tinytag==2.1.1` new. Tests run in Docker:
  `docker compose exec django python manage.py test ... --noinput`.

## Approaches

1. **Chosen: measured duration, fixed customer rate, charge before the call, refund on failure.**
   The charge *is* the reservation, posted under the wallet row lock, so it closes the race exactly
   and can never be lost to a crash. A failed transcription gets a compensating adjustment.
2. **Pass the provider's reported cost through to credits.** Rejected: the bill moves on a provider
   price change or a model swap, and the unit stops being seconds.
3. **Flat credit per voice note.** Rejected: a 5-second "ha" and a 5-minute description cost the
   same while costing us 60× apart.
4. **Check, call, then charge, with a `reserved_credits` counter on the wallet** (the review's
   preferred option). Rejected in favour of 1 — see "Where the review and I disagree". It needs a
   new field, a new reservation lifecycle, and a leak sweeper for reservations stranded by a crash,
   where a stranded *charge* is already visible in the ledger and fixable by the same sweep.

## Design

### 1. Billing: `charge_if_affordable`, charge first, refund on failure

New in `billing/service.py`:

```python
def charge_if_affordable(project, action, *, key, units=1, **row_fields):
    """Check affordability and post the charge under one wallet lock, so concurrent callers cannot
    both pass the check. Returns the transaction (the existing one for a repeated key), None for a
    zero-priced action, and raises InsufficientCredits when the balance would pass the overdraft."""
```

- Order inside one `transaction.atomic()`: zero price → `None` (before any lock, like `charge()`);
  lock the wallet; **existing row for `key` → return it**; then
  `if wallet.balance - price * units < -PricingSettings.get().overdraft_credits: raise InsufficientCredits`;
  then `_post()`.
- The idempotency lookup comes first so a retry of a charge that already succeeded is never refused
  because that same charge lowered the balance.
- Unaffordable raises the existing `billing.service.InsufficientCredits`, so `None` keeps the single
  meaning it has in `charge()` (free action, nothing posted).
- Same overdraft semantics as `shopper_blocked`, so there is still exactly **one** definition of
  affordability in the system.

Used by the transcription path **before** the provider call:

- `units = max(1, ceil(seconds / seconds_per_credit))`. The price is applied once, by the charge
  (`price_of("transcription") × units`); the caller never multiplies by the price.
- `txn = charge_if_affordable(project, "transcription", key=f"transcription:{msg_id}:{index}",
  units=units, conversation=..., trigger_message=msg)`.
- `InsufficientCredits` → no provider call, and the attachment is marked `unaffordable`.
- `None` (transcription priced at 0) → transcribe for free; there is nothing to refund later.
- A retry of the same attachment returns the existing transaction, so it does not charge twice; the
  state check (section 4) makes sure it does not re-call the provider either.

**Failure is refunded.** On provider error, an empty transcript, or silence:

```python
txn = CreditTransaction.objects.filter(idempotency_key=f"transcription:{msg_id}:{index}").first()
if txn:   # no charge row (free, or the charge never happened) → nothing to refund
    grant(project, -txn.amount, "adjust", key=f"transcription_refund:{msg_id}:{index}",
          note=f"Voice note not transcribed ({reason})")
```

The amount comes from the charge row, never from a stored or recomputed figure, so a refund can never
exceed what was taken. `grant()` is idempotent on the key, so a retried refund is a no-op. Net
customer cost is zero and the ledger shows why.

**Why this beats check-then-charge:** the check and the charge share the wallet lock, so two workers
serialise and the second sees the first's debit. The −100 example above cannot happen. It also
removes the "transcript saved, charge lost" window the review flagged, because there is no charge
after the transcript any more.

**Residual window:** billed, then the worker is killed before the provider call. The reconciliation
sweep (section 6) refunds it. Money is never silently taken for a service we did not buy.

### 2. Measuring the duration

`backend/agents/transcription.py`:

- `audio_format(data: bytes) -> str | None` — the container from its first bytes: `OggS` → `ogg`;
  `ftyp` at offset 4 (any brand) → `m4a`; `ID3` or an MPEG layer III frame sync → `mp3` (ADTS AAC
  shares the sync bits but not the layer, so it is not mistaken for MP3); `RIFF…WAVE` → `wav`;
  anything else, AMR included → `None`. The stored file's extension is not used (see Constraints).
- `audio_seconds(data: bytes, fmt: str) -> int | None` —
  `TinyTag.get(filename=f"audio.{fmt}", file_obj=io.BytesIO(data))`, `ceil(tag.duration)`. `None`
  for an unreadable container or a zero/absent duration.
- Either `None` → no provider call, no charge, deterministic failure reply.

### 3. Calling the provider, with the attempt recorded first

`backend/agents/transcription.py`:

- `resolve_transcription_model() -> str` — `ModelConfig.transcription_model` →
  `settings.TRANSCRIPTION_MODEL` → `"elevenlabs/scribe-v2"`, and **only if the name is in
  `SUPPORTED_TRANSCRIPTION_MODELS`**; otherwise log and fall back to scribe-v2.
- `transcribe(data, fmt, model=None) -> Transcript(text, tokens, cost_usd, model, usage)`; `fmt` is
  `audio_format()`'s answer; timeout 45 s. Raises `TranscriptionError` on non-200 or invalid JSON.
  Which `usage` fields exist is decided by the captured fixtures (plan Task 0), not assumed.

Attempt marker, so a killed worker is still visible in the cost report:

1. **Before** the HTTP call, create the `UsageEvent`: `kind="transcription"`, `tokens=0`,
   `cost_usd=None`, `cost_source="unknown"`, `model_name=<model>`, `metadata={"status":
   "started", "seconds": s, "attachment_index": i}`, linked to the conversation and message.
2. **After** the response, `UsageEvent.objects.filter(pk=...).update(cost_usd=..., tokens=...,
   cost_source=..., metadata={... "status": "reported"})`.
3. On an exception, the same update with `{"status": "failed"}` and the error type only.
4. **Recovery:** an attempt still marked `started` is an unknown charge. The sweep counts it as
   `unknown` and leaves `cost_usd` NULL — it is never rewritten into an ordinary failure.

No new table: the attempt record is a `UsageEvent` row, which is already per-call, already linked to
the message, and already what `coverage()` reads.

### 4. Where it runs: one task per attachment, then a message-level check

```
store_message_media(msg_id, reply)                # storage only, unchanged
    store attachments
    if audio attachments and reply and transcription on:
        for each audio attachment, stored or not: transcribe_attachment.delay(msg_id, index)
    elif reply:
        schedule_reply(msg)

transcribe_attachment(msg_id, index)              # soft 60 s, hard 90 s, queue "replies"
    ... one attachment, one provider call ...

maybe_schedule_reply(msg_id)                      # called by every attachment task at the end
    every audio attachment has a terminal state → schedule_reply(newest pending inbound message)
```

- One task per attachment means N attachments can never exceed the time limit together, and each
  attachment has its own claim, state, charge and refund key.
- Terminal states, kept in one constant: `transcribed`, `failed`, `failed_uncertain`, `empty`,
  `unmeasurable`, `over_cap`, `unaffordable`.
- The task body, per attachment:
  1. Claim: `cache.add(f"transcribe:{msg_id}:{index}", 1, timeout=180)`; lost claim → return.
  2. Read the state under a row lock (`update_message_meta`, section 5).
     **Any terminal state → return** (no charge, no call). `processing` from an earlier run →
     **do not call again** (at-most-once), mark `failed_uncertain`, refund.
  3. No stored `path` (storage failed) → mark `failed`, no charge. Otherwise read the bytes from R2,
     detect the format, measure; mark `unmeasurable` / `over_cap` when that fails or exceeds
     `TRANSCRIPTION_MAX_SECONDS`. No charge has happened yet, so nothing to refund.
  4. Charge first (`charge_if_affordable`). `InsufficientCredits` → mark `unaffordable`, no provider
     call.
  5. Write `{"status": "processing", "model", "seconds", "units", "started_at"}`, then mark the
     attempt, then call.
  6. Usable text → write `text` (appended, see section 5), `{"status": "transcribed", ...}`.
     Unusable → `empty` and refund; provider error → `failed` and refund.
  7. **Any other exception** anywhere above (DB, R2, soft time limit) → mark `failed`, refund if a
     charge row exists, log the exception type only. A note never stays without a terminal state
     because of an error in our own code.
  8. Always, in `finally`: `maybe_schedule_reply(msg_id)`.
- `maybe_schedule_reply` schedules `pending_turn(conversation)[-1]`, the **newest** pending inbound
  message, not the audio message. Reason: when a text arrives while a voice note is transcribing, the
  text's own reply task sees the untranscribed note and returns (section 6); if the voice note then
  scheduled itself, its task would skip as "batched" behind the newer text, and nobody would answer
  until `recover_stuck_replies` five minutes later. Guarded once per message by
  `cache.add(f"transcribe-done:{msg_id}", 1, timeout=3600)`.

The transcription task runs on the `replies` queue for the pilot, which needs an explicit entry in
`CELERY_TASK_ROUTES` (it routes by task name; without it the task lands on the ingestion queue). A
dedicated `transcription` queue is the first thing to add if voice-note volume starts delaying text
replies (see "Where the review and I disagree").

### 5. Message text and metadata

**Text is never overwritten.** `text = (msg.text + " " + joined_transcripts).strip()`, and
`meta["transcript_texts"] = {index: text}` keeps each attachment separately for audit. In practice
WhatsApp and Messenger audio carry no caption, so the append is a no-op today; the rule exists so a
future channel with captions cannot lose typed text.

**Paid-for content survives.** `format_message_with_context` suppresses the generic
`[Message includes attachments: audio]` note only when the message's text is non-empty.

**Atomic metadata updates.** New helper in `channels_app/media_store.py`:

```python
def update_message_meta(message_id, fn):     # fn(meta) -> new meta, under select_for_update
```
Inside `transaction.atomic()`, `Message.objects.select_for_update().get(pk=...)`, apply, save with
`update_fields=["meta", "text"]`, return the new meta. Every transcription state change goes through
it, so the transcription writers (two attachment tasks on one message, the sweep) cannot clobber each
other. The Redis claim prevents ordinary duplicates; the row lock is the durable source of truth.

It does **not** protect against `image_reply`, which writes `meta` without the lock
(`image_reply.py:138`, `:400`). That is safe today because `image_reply` runs inside `process_reply`,
and the guard in section 6 returns before it while any audio attachment is still non-terminal. If
`image_reply` ever runs earlier, it must move onto `update_message_meta` too.

### 6. Recovery, and the one sweep

- **`recover_stuck_replies` guard.** It calls `process_reply`, which would answer an untranscribed
  note. `process_reply` gains an early branch: when the turn has an audio attachment whose state is
  not terminal, enqueue `transcribe_attachment` for it and return, leaving the inbound `pending`.
- **`reconcile_transcriptions`** (new beat task, every 600 s), scanning messages created between 2
  hours and 10 minutes ago that have a transcription entry still `processing` (the lower bound keeps
  the batch from filling up with old finished rows, the same window idea as `recover_stuck_replies`):
  - attempt still `started`, no transcript → mark `failed_uncertain`, refund (only if a charge row
    exists, amount from that row), then `maybe_schedule_reply`.
  - transcript present but state not `transcribed` → mark it, no refund.
  This is the only new scheduled job, and it is the review's "billing reconciliation" — it works
  because the charge always exists before the transcript does.
- `recover_stuck_replies` already covers the "charged, killed before the reply" case: the inbound
  stays `pending`, and recovery answers it.

### 7. Reaching the reply pipeline

- `channels_app/media.py`: `wants_audio_reply(attachment_info) -> bool` — any `type == "audio"`
  attachment and transcription is enabled for this project.
- Both webhook handlers OR it into `wants_reply` and record `meta["audio_reply"] = True`
  (`service/whatsapp_messaging.py:69`, `service/facebook_messing.py:180`). Instagram shares the
  Messenger handler, so it is covered by the same path and gets its own test.
- `reply._newer_text_message` gains `| Q(meta__audio_reply=True)`.

**Mixed content, per attachment:**

| message | behaviour |
|---|---|
| text only | unchanged |
| audio only, transcribed | normal agent reply |
| text + audio, transcribed | typed text kept, transcript appended, normal reply |
| two audio, both transcribed | transcripts appended in attachment order |
| one succeeds, one fails | reply from what is usable; the failed note is reported in the reply context |
| all audio fail, no typed text | deterministic failure reply, no agent call, no `reply` charge |
| audio fails, typed text exists | normal reply on the typed text |
| some attachments unaffordable | those are skipped with no provider call; the reply uses the rest |
| all audio unaffordable, no typed text | the existing out-of-credits handoff (staff can listen to the note), not the failure reply |

Both branches read the per-attachment states in `meta["transcription"]`; there is no separate
"failed" flag. "No typed text" means no message in the turn has non-empty `text` (a successful
transcript counts as text, since it was appended).

- **All unaffordable:** no text in the turn and at least one audio attachment is `unaffordable` →
  `out_of_credits = True`, so `_reply` takes its existing handoff path.
- **All failed:** no text in the turn, every audio attachment terminal and none `transcribed` →
  `reply_text = settings.TRANSCRIPTION_FAILED_MESSAGE(_BN)` in the same `if/elif` chain that already
  skips the agent for out-of-credits and photo timeouts. The existing tail saves and delivers it;
  `agent_replied` stays false, so there is no `reply` charge. No new delivery helper.
- Language: the turn has no text, so the Bengali choice looks at the conversation's last 10 inbound
  messages, the same script check `_reply` already does.

### 8. Pricing surface

- `billing.api.PricingView`: the `transcription` row also carries
  `"per_seconds": int(seconds_per_credit)`.
- `PricingSection.tsx`: "Each voice note costs N credit(s) for each started 5 seconds of audio, with a
  minimum of one. Nothing is charged if we cannot transcribe it." N comes from
  `price_of("transcription")`, so it is the effective price.
- `channels_app/api.py:_message_credits`: add `"transcription"` to the `action__in` tuple, and skip a
  transcription charge whose `transcription_refund:` key exists (one extra query per conversation).
  Otherwise a refunded note still shows its charge in the conversation view. The wallet's transaction
  list keeps both rows, which is the audit trail.

### 9. Settings, migrations, provider sync

| where | change |
|---|---|
| `core/settings.py` | `TRANSCRIPTION_ENABLED=False`, `TRANSCRIPTION_PROJECTS=[]` (pilot allowlist), `TRANSCRIPTION_MODEL=""`, `TRANSCRIPTION_MAX_SECONDS=300`, `TRANSCRIPTION_FAILED_MESSAGE` + `_BN` |
| `billing.service` | `charge_if_affordable()` |
| `billing.models` | `ACTIONS["transcription"]`, `PricingSettings.transcription_seconds_per_credit = 5`; one migration seeding the `ActionPrice` row |
| `agents` | `SUPPORTED_TRANSCRIPTION_MODELS`; `ModelConfig.clean()` rejects anything else; data migration seeding the three catalogue rows |
| `agents.pricing` | merge `/api/v1/models` with `?output_modalities=transcription` |
| `usage` | `"transcription"` in `KIND_CHOICES` and in `record_model_usage`'s kind literal |
| `channels_app` | `transcribe_attachment`, `maybe_schedule_reply`, `update_message_meta`, `reconcile_transcriptions` |
| `core/settings.py` `CELERY_TASK_ROUTES` | `channels_app.media_store.transcribe_attachment` → `replies` |
| `requirements.txt` | `tinytag==2.1.1` |

`TRANSCRIPTION_ENABLED` is the global kill switch; `TRANSCRIPTION_PROJECTS` is the pilot allowlist
(empty list = every project, once enabled). A project is on when
`TRANSCRIPTION_ENABLED and (not TRANSCRIPTION_PROJECTS or project.slug in TRANSCRIPTION_PROJECTS)`.

## Money, assumptions stated

Per 60 seconds, against `usd_per_credit` = $0.005, charged 12 credits = $0.060:

| model | provider cost per 60 s | coverage |
|---|---|---|
| `elevenlabs/scribe-v2` | $0.00366 ($0.000061/s) | 16.4× |
| `x-ai/grok-stt-2.0` | $0.00168 ($0.000028/s) | 35.7× |
| `google/gemini-3.5-transcribe` | $0.0067 at the stated assumption | ~9× |

Gemini assumption, written out because rev 2 and rev 3 both got it wrong: Gemini bills audio at 32
input tokens per second (documented for the 1.5 and 2.x models; confirm for 3.5 from the Task 0
fixture), so 60 seconds ≈ 1,920 input tokens, and a transcript of ~4 tokens/second ≈ 240 output
tokens: `(1,920 × 2 + 240 × 12) / 1,000,000 = $0.0067`. That is still above the 5× target, but it
has the least margin of the three. `usage.cost` replaces the estimate on the first real call either
way.

## Where the review and I disagree

**1. `reserved_credits` on the wallet, instead of charging first.** The review's preferred fix and
my rev-3 fix produce the same customer outcome, but the failure modes differ. A stranded
*reservation* — reserved, then the worker dies — silently reduces the project's available credits
forever unless a sweeper stores and releases the amount. A stranded *charge* is a row in the ledger,
visible in the customer's own transaction list, and one `grant()` fixes it. The charge-first version
also needs no new field, no new lifecycle, and no coupling between `charge()` and a reservation
counter. The one cost is ledger noise on failures (one `adjust` row with a note), which is rarer and
more debuggable than an unexplained credit shortfall.

**2. Order for the billing record.** The review asked for `billing_status: pending → charged` after
the transcript. Rev 3 has no such window to track: the charge is written before the provider is
called, so "transcribed but unbilled" cannot exist. The sweep's only job is the opposite direction —
refund a charge whose call never produced a transcript.

**3. A dedicated `transcription` queue now.** The review lists it as "should fix before general
release", and I agree with that severity, so it is not in the pilot. A queue with no worker is a
worse failure than a shared queue: the task would sit unclaimed, `recover_stuck_replies` would keep
re-enqueuing, and voice notes would never be answered. The pilot runs on `replies` with a 45 s
provider timeout and per-attachment tasks; the queue split ships with the compose service that
consumes it, before general rollout.

**4. A separate task for the aggregation step.** `maybe_schedule_reply` is called by the last
attachment task to finish, which is a first-writer-wins check under the same row lock, not a chord.
A Celery chord would be a third dependency for the same guarantee.

## Calls I made (for you to check)

- **1 credit per started 5 seconds, minimum 1**, multiplied by the live `ActionPrice`. Signed off in
  the review.
- **Charge before the call, refund on failure**, rather than a reservation counter.
- **Cap at 300 s**; above it, no provider call and a deterministic failure reply.
- **Transcription off by default**, pilot via the project allowlist.
- **No per-tenant switch beyond the allowlist** — the allowlist is temporary and removable.
- **At-most-once per attempt.** A killed call is not retried; the attachment fails and the customer
  gets the failure reply. Cheaper and simpler than a retry policy for an uncertain charge.
- **`tinytag`, not `mutagen`** (licence), and AMR rejected rather than normalised. The format comes
  from our own 5-line byte sniff, not from the file extension or tinytag's sniffing.
- **An audio-only turn we could not afford hands off to staff**, rather than telling the customer we
  could not understand them.
- **The three older catalogue rows stay**, but `ModelConfig.clean()` now refuses to save any model
  outside `SUPPORTED_TRANSCRIPTION_MODELS`.

## Tests

`backend/agents/tests/test_transcription.py`
- `audio_format` on the recorded WhatsApp (Ogg) and Messenger (MP4) fixtures, on an MP4 header with a
  non-`M4A` brand (`isom`), on WAV, and on garbage/empty/AMR → `None`.
- `audio_seconds` on the recorded fixtures and a generated WAV; rounds up; garbage → `None`.
- `transcribe` parses `text` and `usage.cost` from the captured responses (Task 0) for all three
  models; 500 raises; missing cost → `None`; the response body never reaches a log record.
- `resolve_transcription_model` order, and an unsupported `ModelConfig` pick falls back to scribe-v2.
- `ModelConfig.clean()` rejects an unsupported transcription model.

`backend/agents/tests/test_pricing_sync.py`
- a model listed only under `output_modalities=transcription` is priced.

`backend/billing/tests/test_service.py`
- `charge_if_affordable`: posts under the lock; raises `InsufficientCredits` when the charge would
  pass the overdraft; returns `None` for a zero-priced action; **two threads with room for one charge
  can only charge once**; a repeated key returns the existing row without a second debit, **even when
  the balance is now below what a new charge would need**.

`backend/channels_app/tests/test_audio_transcription.py`
- success: text appended, state `transcribed`, attempt `UsageEvent` finalised with the reported cost,
  charge `ceil(s/5)`, reply scheduled once.
- price 2 credits: the charge is `2 × ceil(s/5)`, not `4 ×` (price applied once).
- price 0: transcribed, no charge row, no refund row, not `unaffordable`.
- a stored `.bin` Messenger file is transcribed with format `m4a` from its bytes.
- after a refunded `failed` run, a second run makes no provider call and no new rows.
- an unexpected exception (R2 read raises) ends in `failed`, refunded, reply scheduled.
- an audio attachment with no stored `path` ends in `failed` with no charge.
- typed text + audio: typed text preserved, transcript appended.
- two attachments: two charges with distinct keys, transcripts in order, one reply.
- one of two fails: the reply uses the usable transcript; the failed one is refunded only.
- empty transcript, provider error, unmeasurable duration, over-cap duration: no provider call
  (mocked) or no charge after refund, deterministic failure reply, attempt recorded.
- unaffordable: no provider call, state `unaffordable`, reply uses typed text or the failure message.
- second run: no second charge, no second provider call.
- interrupted attempt (`processing`): no second provider call, refunded.
- the reply is scheduled on every terminal outcome, including after an unexpected exception.
- `transcribe_attachment` routes to the `replies` queue.
- `wants_reply` is true for audio only when transcription is on for that project.
- Instagram: an audio attachment gets `audio_reply` and the same task.

`backend/channels_app/tests/test_reply_pipeline.py`
- `recover_stuck_replies` enqueues transcription, not a reply, for an untranscribed audio message.
- the failure turn sends the canned message, calls no agent, charges no `reply`.
- `reconcile_transcriptions` refunds a stranded charge and schedules the reply; it ignores
  messages older than 2 hours.
- a voice note followed by a text while it transcribes: one reply, covering both, scheduled as soon
  as the transcription ends (not after `recover_stuck_replies`).
- audio-only turn, all `unaffordable`: handoff message, no agent, no failure message.
- the conversation view hides a refunded transcription charge.

`backend/billing/tests/test_api.py`
- the `transcription` row exists at 1 credit; the pricing endpoint carries `per_seconds`.

### Input format matrix

Each cell is a test with a committed fixture against a mocked provider call, repeated against each
enabled model's expected payload:

| input | local duration | accepted by |
|---|---|---|
| Ogg/Opus | yes | scribe, grok, gemini — verified per model before enabling |
| M4A/MP4 | yes | verified: `voic_m4a.m4a` (`ftyp` brand `mp42`, not `M4A`) accepted by all three models, HTTP 200, cost present. This is why the container is sniffed for `ftyp` and not for the brand |
| MP3 | yes | verified per model before enabling |
| AMR | no | rejected, no provider call |

A model is not enabled for a project until its row in this table has passing fixtures.

## Acceptance criteria

| scenario | expected |
|---|---|
| duration unmeasurable / AMR | no provider call, no net charge, canned failure reply |
| audio over 300 s | no provider call, no net charge, canned failure reply |
| project cannot afford it | no provider call, no charge, handoff (audio only) or typed-text reply |
| retry of an attachment that was already charged, wallet now low | existing charge returned, no refusal, no second call |
| Messenger / Instagram voice note stored as `.bin` | format read from the bytes, transcribed |
| text sent while a voice note transcribes | one reply covering both, right after the transcription |
| error in our own code mid-task | `failed`, refunded, canned failure reply |
| two voice notes on a 20-credit wallet with 50 overdraft | exactly one call when only one is affordable; never below the floor |
| provider error / empty transcript | no net charge (refunded), attempt recorded, canned failure reply |
| two workers on one attachment | one claim, one provider call, one charge |
| task killed mid-call | attempt visible as unknown, no retry, refund, canned failure reply |
| task killed after the charge, before the call | sweep refunds, reply still sent |
| task killed after the reply was scheduled | `recover_stuck_replies` answers without re-billing |
| two audio attachments | two independent charges, joined transcript, one reply |
| typed text + audio | typed text preserved |
| Bengali / Banglish transcript | stored verbatim; quality benchmarked before enabling |

## Rollout

1. Ship with `TRANSCRIPTION_ENABLED=False`. Nothing changes for any project.
2. Before any code (plan Task 0): record our own voice notes through the dev WhatsApp number and dev
   Messenger page (never customer files from `chat_media/`), and capture one real response per model
   for them; fix the duration and cost fields from those fixtures. Then pass the format matrix; benchmark Bengali/Banglish word error rate, latency and failure rate (a
   human call, not a test).
3. Publish the price wording before enabling, so the rule is visible before the first bill.
4. `TRANSCRIPTION_ENABLED=True` with `TRANSCRIPTION_PROJECTS=["<pilot slug>"]`. Watch volume, failure
   rate, refund rate, average `usage.cost` and `coverage()` for that project for a week.
5. Empty the allowlist to go global. Revisit `seconds_per_credit` if coverage on the running model
   drifts from 5×.
6. Before general release: split the `transcription` queue (section 4).

## Privacy

Customer audio goes to OpenRouter and from there to the model provider — new third-party processing
that needs to be covered by the platform's terms. The transcript is stored in `Message.text` and
`meta`, the same exposure as a typed message. Neither audio bytes nor transcript text is written to a
log; error paths log the message id and the exception type only.
