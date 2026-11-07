# Meta Self-Serve Connect (Messenger, Instagram, WhatsApp): Draft Design

Status: **DRAFT, not approved.** Brainstorming paused 2026-11-07. Nothing is built. No approach has been picked yet.
Builds on: `docs/meta-public-integration.md` (Messenger/Instagram analysis, App ID `1257774078107684`).

## Intent

Today a customer connects a channel by pasting `page_id`, tokens and a verify token by hand, and pasting our callback URL into their own Meta app. **Manual connect stays.** We add a self-serve Connect flow so any business can click Connect, approve access, and have messages arrive, with no Meta developer account.

Goal: Facebook Messenger, Instagram DMs and WhatsApp, all self-serve.

## What exists (verified in code 2026-11-07)

- One webhook for all three channels at `/webhooks/meta/` (`channels_app/views_facebook.py`). Signature is checked against `META_APP_SECRET`. For WhatsApp it also accepts a per-number `app_secret`.
- Routing: `meta_credentials(account_id)` matches `credentials.page_id` or `instagram_account_id`. `whatsapp_credentials(phone_number_id)` matches the WhatsApp number. Both go through `ChannelCredential` (JSON `credentials` field, one project each).
- Sending: `facebook_client.py` (`send_text_message`, `send_whatsapp_text`, `get_user_name`). Graph `v21.0` is hardcoded in `facebook_client.py:8` and `:110`. `v18.0` is in `models.py:485` and `:495`.
- Manual UI: `frontend/app/(dashboard)/channels/page.tsx` (771 lines), driven by each platform's `credential_schema`.
- `META_APP_ID` and `META_APP_SECRET` already exist in `core/settings.py:178-179`.
- Legal pages exist: `/privacy`, `/terms`, `/dpa`, `/data-deletion`.

## Gaps

**Messenger and Instagram** (details in `meta-public-integration.md` section 4): Facebook Login redirect with signed `state`, code to long-lived token, `/me/accounts` Page picker, `POST /{page}/subscribed_apps`, disconnect, deauthorize callback with `signed_request`, token encryption, one global verify token (current lookup is per channel in `service/facebook_messing.py:55-59`), profile name and picture fields.

**WhatsApp** (not covered by the earlier doc):
- Embedded Signup **v4**. v2 and v3 are deprecated on 2026-10-15.
- Server steps after the popup: exchange the code, subscribe the customer's WABA to our app (`/{waba}/subscribed_apps`), register the phone number, and handle the `account_update` webhook.
- Credentials today ask for the customer's own app id, secret and verify token. A self-serve connection uses our app.
- Tech Provider status: business verification plus Advanced Access to `whatsapp_business_messaging` and `whatsapp_business_management`. A screen recording is part of the review.
- **Blocker:** the app is type "Other", and the Meta MCP says it cannot request those two permissions. The WhatsApp use case and a linked Business Portfolio must be added in the App Dashboard first.

**Cross-channel health:**
- `value["statuses"]` is ignored (a `ponytail:` note sits in `service/whatsapp_messaging.py:22`), so failed sends go unnoticed.
- No template messages after the 24-hour window (error 131047).
- No detection of an expired or revoked token (error 190), so there is no reconnect state.
- One Graph version constant is needed.

## Approaches considered

**A. Two connect flows, one backend module (recommended).**
- Messenger and Instagram: server-side Facebook Login redirect, then a Page picker.
- WhatsApp: Embedded Signup popup (JS SDK).
- New `channels_app/meta_connect.py` holds the shared Graph helpers.
- Reuse `ChannelCredential` with a `connected_via: "oauth"` marker and a status field.
- Trade-off: two UX patterns, but each is the documented, reviewable path.

**B. One popup for all three (Facebook Login for Business, single configuration).**
- One button.
- **Unconfirmed** that a single configuration can cover Pages, Instagram and WhatsApp. Riskier for App Review. Needs research before it can be considered.

**C. Phase by approval lead time.**
- Phase 1: Messenger and Instagram (standard review).
- Phase 2: WhatsApp (Tech Provider verification takes longest).
- A delivery order that combines with A.

**Recommendation: A, delivered as C.** Awaiting the user's choice (or a request to research B first).

## Open items

1. User picks the approach (A as C, or research B).
2. Who connects: businesses that already have a Page and WhatsApp Business, or ones with neither. Embedded Signup can do both; this only affects copy and onboarding help.
3. Dashboard work (user, not code): add the WhatsApp use case, link the Business Portfolio, finish business verification, add the redirect URI, privacy, terms and data-deletion URLs, and add the Instagram permissions to the review request.
4. FE design: use `/frontend-design:frontend-design`. Offer the visual companion for the Connect card, picker and reconnect states.
5. Not yet checked: whether one Facebook Login for Business configuration can cover all three channels, and whether a Meta `user_id` can be stored in the existing JSON field without a migration.

## Next steps when resuming

1. Get approval on the approach, then write the full design from this draft.
2. Run the builder check, get the design approved, then invoke `superpowers:writing-plans`.
