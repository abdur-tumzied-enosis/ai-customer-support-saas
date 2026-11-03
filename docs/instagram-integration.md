# Connect Instagram DMs to AutoMagic Pro

How Instagram Direct messages reach our webhook and get AI replies, what has to be set up
on the Meta side, and what the backend still needs.

Related: [facebook-add-page-via-api.md](facebook-add-page-via-api.md),
[facebook-webhook-debugging.md](facebook-webhook-debugging.md)

---

## Status

Backend done. What's left is Meta setup + creating the credential.

| Piece | Where |
|---|---|
| `instagram` channel / platform choice | `channels_app/models.py`, migration 0014 |
| Webhook: `object: "instagram"`, echo skip | `service/facebook_messing.handle_facebook_message` |
| Route | `/webhooks/meta/` serves both (`object` tells them apart) |
| Reply sending, 990-byte parts | `reply.SENDERS["instagram"]`, `facebook_client.split_text(max_bytes=...)` |
| Credential lookup by page id or IG account id | `models.meta_credentials`, `tasks.get_facebook_credential` |
| Name lookup `name,username` | `facebook_client.get_user_name(fields=...)` |
| Staff reply 24h window | `api.ConversationStaffReplyView` |
| Reply debounce | `INSTAGRAM_REPLY_DEBOUNCE_SECONDS` (default 3) |
| Tests | `channels_app/tests/test_instagram.py` |

**Create the credential** with the existing generic endpoint (no Instagram-specific endpoint):

```http
POST /api/channels/channels
{"project_slug": "...", "name": "Instagram", "channel": "instagram", "knowledge_base_id": "...",
 "credentials": {"instagram_account_id": "1784...", "page_id": "...", "page_access_token": "EAA...",
                 "meta_app_id": "...", "meta_app_secret": "...", "verify_token": "..."}}
```

`knowledge_base_id` is required in practice: the webhook drops messages for credentials without one.

Not handled yet: `is_deleted` (Meta terms want our copy deleted; today the dedupe on `mid`
just skips it), attachments-only messages (stored, no reply, same as Messenger).

---

## Which Meta API

Meta has two ways to do Instagram messaging. We use the first.

| | **Messenger Platform for Instagram** (ours) | Instagram API with Instagram Login |
|---|---|---|
| Needs a Facebook Page | Yes, linked to the IG account | No |
| Host | `graph.facebook.com` | `graph.instagram.com` |
| Token | Page access token (same as Messenger) | Instagram user token |
| Send | `POST /me/messages` (same as Messenger) | `POST /{ig_id}/messages` |
| Fit with our code | Reuses webhook, signature check, send client | New login flow, token type, client |

Switch only if customers have IG accounts with no linked Facebook Page.

---

## Meta setup

### 1. Instagram account

- Must be a **Professional** account (Business or Creator).
- Must be **linked to a Facebook Page** (Page settings → Linked accounts → Instagram).
- In the Instagram app: Settings → Messages and story replies → Message controls →
  Connected tools → **Allow access to messages: ON**. Without this no webhook fires.
- Creator accounts must make one Conversations API call before webhooks start.

### 2. App dashboard (one time, shared by every account)

- Add the **Instagram** product (Messenger API settings for Instagram).
- Webhooks → object **Instagram** → callback URL + verify token. The same URL as the
  Page webhook works (`/webhooks/meta/`): same app, same `META_APP_SECRET`, so the
  `X-Hub-Signature-256` check passes. We branch on `payload["object"]`.
- Subscribe field **`messages`**. Optional later: `messaging_postbacks`,
  `message_reactions`, `messaging_seen`.
- App must be **published (Live)** to receive webhooks at all.

### 3. Permissions on the Page token

```
instagram_basic, instagram_manage_messages, pages_manage_metadata,
pages_show_list, business_management
```

Add them to `SCOPES` in the OAuth flow from
[facebook-add-page-via-api.md](facebook-add-page-via-api.md). Keep `pages_messaging` if the
same page also does Messenger.

Access levels: with **Standard Access** only people with a role on the app (and on the IG
account) trigger webhooks. Real customers need **Advanced Access** for
`instagram_manage_messages` (App Review + Business Verification). Meta also requires a
human escalation path, which `escalate_to_human` covers.

### 4. Per account (each customer)

```bash
GRAPH=https://graph.facebook.com/v21.0

# Instagram account id linked to the page
curl "$GRAPH/$PAGE_ID?fields=instagram_business_account&access_token=$PAGE_TOKEN"
# → {"instagram_business_account": {"id": "1784..."}, "id": "<page_id>"}

# Subscribe the page to the app (skip if already done for Messenger)
curl -X POST "$GRAPH/$PAGE_ID/subscribed_apps?subscribed_fields=messages&access_token=$PAGE_TOKEN"
```

Then save a `ChannelCredential` with `channel="instagram"` and credentials
`{instagram_account_id, page_id, page_access_token}` (see backend step 6).

---

## Webhook payload

Same shape as Messenger, different `object` and ids:

```json
{
  "object": "instagram",
  "entry": [{
    "id": "IGID",
    "time": 1569262486134,
    "messaging": [{
      "sender":    {"id": "IGSID"},
      "recipient": {"id": "IGID"},
      "timestamp": 1569262485349,
      "message": {
        "mid": "MESSAGE-ID",
        "text": "hi",
        "attachments": [{"type": "image", "payload": {"url": "..."}}],
        "reply_to": {"mid": "..."},
        "is_echo": true,
        "is_deleted": true,
        "is_unsupported": true
      }
    }]
  }]
}
```

- `IGID` = our Instagram account id (not the page id). `IGSID` = Instagram-scoped customer id.
- `is_echo`: sent for messages the business sends, including our own replies. Sender is
  `IGID`, recipient is the customer, so the credential lookup by recipient finds nothing
  and the event is skipped. Keep it that way, or skip `is_echo` explicitly.
- `is_deleted`: customer deleted a message. Meta terms require deleting our copy.
- GIFs, stickers and view-once media send no webhook or an `ephemeral` attachment with no URL.
- Attachment types: `audio`, `file`, `image`, `share`, `story_mention`, `video`, `ig_reel`, `reel`.

---

## Backend changes (done)

See the Status table. Design notes:

- One webhook handler for both channels: `payload["object"]` picks the channel (the contact's
  platform), and `models.meta_credentials(recipient.id)` finds the credential by `page_id` or
  `instagram_account_id`.
- **One Instagram connection also covers its linked page.** Messenger messages to that page
  (`object: "page"`) are answered with the Instagram credential's page token and knowledge base,
  as `facebook` conversations. A separate `facebook` credential for the same page wins if one exists.
- Sending reuses `send_text_message` (`/me/messages`, Page token). Instagram limit is
  1000 UTF-8 **bytes**, so Bengali or emoji text splits much sooner than English.
- `is_echo` events are skipped for both channels.

Same rules as Messenger: 24-hour reply window from the customer's last message, same
dedupe on `mid`, same reply pipeline.

---

## Checklist when nothing arrives

1. Signed-in IG account has "Allow access to messages" on?
2. App Live, Instagram webhook `messages` field subscribed?
3. `POST /{page_id}/subscribed_apps` done for the linked page?
4. Sender has a role on the app (Standard Access)?
5. Webhook log shows `object: "instagram"`? If yes and nothing stored: credential lookup —
   `instagram_account_id` must equal `entry[].id` / `recipient.id`.
