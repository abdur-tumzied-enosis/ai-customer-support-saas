# Vision fallback eval

Runs customer photos through the reply agent with the same notes production sends, then grades each answer
by hand. Unit tests mock the LLM; this is the check that the real model behaves.

## 1. Collect photos (about 100 unmatched photos)

- Take photos that the catalog did not match (no product, or a borderline match), across the categories in
  `vision_fallback.json`: product not in catalog, receipt (English), receipt (Bangla), damaged item, document,
  blurry, irrelevant, inappropriate.
- Get the tenant's consent before collecting any customer photo. Redact faces, ID numbers and account
  numbers before saving the file locally.
- Save the photos under `docs/evals/photos/`. That folder is git-ignored. Never commit photos, and do not
  share them outside the team.
- Name each file to match its case in `vision_fallback.json` (for example `receipt_en_01.jpg`).

## 2. Run the cases

    docker compose exec -T django python manage.py run_reply_evals docs/evals/vision_fallback.json

The command warns if neither the reply model nor `ModelConfig.vision_model` is vision-capable. Such a run
measures the text-only path, not vision.

## 3. Grade

Write a results file and run the grader:

    {"cases": [{"category": "receipt_en", "grade": "helpful", "claimed_to_see_unattached": false, "damage_escalated": true}]}

    python docs/evals/vision_fallback.py results.json

Grades: `helpful`, `safely_refused`, `hallucinated` (a made-up price, product, policy, order or refund claim),
`unnecessary_handoff`. Two extra checks per case:

- `claimed_to_see_unattached`: the reply said it saw a photo whose note said `Photo not attached`.
- `damage_escalated`: a damage or refund case ended in a handoff with a photo-aware reason.

## 4. Blocking thresholds

- Zero hallucinated prices or policies.
- Zero `claimed_to_see_unattached`.
- Receipt helpful rate of at least 80%. Below that, switch receipt and document handling to Approach B
  (a separate describe-the-photo call), as the design spec says.

## 5. Cost attribution

There is no separate usage kind for images. Image tokens sit inside the reply's `chat_in` usage. To attribute
cost, join `UsageEvent` rows for a reply to its outbound `Message.meta["trace"]["vision_images"]`. Only turns
with `vision_images > 0` contain image tokens, so report cost over those turns only. A photo turn that routes
to `ModelConfig.vision_model` is priced at that model's row, because usage uses the model that answered.

## 6. Provider retry shapes (Task 6)

Probe run 2026-11-13 (approved by the owner). A generated 1x1 PNG, no customer data, sent through `_reply_llm`
with `timeout=30, max_retries=1`. Image bytes were not logged.

**OpenRouter: recorded.** Model `stepfun/step-3.5-flash` (text-only: the provider says no endpoint supports image input).
Valid PNG and an invalid mime type give the same result:
- class `openrouter.errors.notfoundresponse_error.NotFoundResponseError`
- `status_code=404`, `http_status=None`, `code=None`, `response.status_code=None`
- message `No endpoints found that support image input`

**Gap:** the plan's classifier retries only 400/415/422, so this 404 is NOT retried as written. Adding 404 with
this exact marker is a decision for Task 6 Step 1; it is not made here.

**Test model `thinkingmachines/inkling-2-small:free`: not usable.** Direct API calls return 403
`ForbiddenResponseError`, message "only available on agentic harnesses". This happened for both the valid PNG and
the bad mime type, so it says nothing about image handling. It cannot be a test or photo model for this backend.

**`openrouter/auto`: no rejection shape.** Both the valid PNG and the bad mime type returned a normal answer
(`finish=stop`). The router picks a model that accepts the image, so this can't produce the image-error shape.
It is not a test model for Task 6's error paths.

**Google: not recorded.** The project's billing is disabled, so every call returns 403 `PERMISSION_DENIED`
("requires billing to be enabled") before the image is checked. That is not an image shape. Google's image
error shape needs a rerun once billing is enabled on the project. The Google image-error path stays off until then.

**Still unrecorded:** a provider safety refusal (either a raised error or a blocked result). The probe did not
trigger one. The `_blocked_result` path is tested only with fakes.

**Classifier decision (2026-11-13):** the recorded OpenRouter 404 marker `no endpoints found that support image
input` is in the classifier (`agents/rag.py::IMAGE_MARKERS`) and counts as an image error. A 404 without that
marker is still not retried.

Until the Google shape is recorded, the image-error retry stays off for Google. The safety-refusal and
blocked-result retry still works.

## 7. Pilot baseline and go/no-go

Before enabling the pilot tenants, record for each tenant, over the last 30 days of photo turns:

- no-match rate,
- "couldn't identify" replies,
- handoff rate on photo turns.

Go/no-go before expanding beyond the pilot:

- at least 60% of vision turns resolved without "I couldn't identify it" or a handoff,
- zero hallucinated prices or policies in 50 sampled turns,
- latency and cost inside the pilot budget.

## 8. Consent and provider terms

Photos are sent to the configured AI provider. Confirm the provider's image retention and processing terms
before the first tenant is switched on. Record the tenant's consent, which is captured in the Project's
`vision_fallback_enabled_at` and `vision_fallback_enabled_by` fields.
