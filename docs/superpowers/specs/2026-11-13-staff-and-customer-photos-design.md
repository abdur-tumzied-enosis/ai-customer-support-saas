# Staff photos out, customer photos visible

Date: 2026-11-13. Status: design, revised after builder review, not built.

## Why

Two gaps in the dashboard conversation view:

1. **Staff cannot send a photo.** Staff can reply with text only (`ConversationStaffReplyView`). When a customer sends
   a photo of the item they want, or asks "which of these?", the only way to answer with an image today is to leave
   the dashboard and use the Meta inbox or WhatsApp directly.
2. **Nobody can see the customer's photo.** Every channel already downloads and stores customer photos in R2
   (`media_store.store_message_media`, regardless of whether image search is on). The dashboard API reduces that to
   `has_photo: true` and the chat renders the literal text "📷 Photo". Staff replying from the dashboard are blind to
   the photo the customer is talking about.

## Goal

- Staff uploads a photo from their computer in the composer and it is delivered to the customer on the
  conversation's channel (Facebook, Instagram, or WhatsApp), for test chats and the widget as a stored message.
- Customer photos are shown as images in the dashboard chat, on every channel, whether or not image search is on.
- Staff-sent photos are shown as images too, so the thread reads correctly.
- A customer who sends several photos has all of them shown, not just one.

Non-goals:

- No product-catalog picker. Staff uploads a file from their computer; picking an existing catalog photo is a later
  feature.
- No video, document, or audio sending. Photos only (JPEG/PNG/WebP upload).
- No changes to image search, the vision fallback, embeddings, or catalog behavior.
- No new database field. `Message.attachment_info` already holds a list of `{type, path}` entries.
- No change to the bot's reply pipeline: the agent still never sends photos.
- No product photos in the outgoing message: this sends the staff member's own file, not a catalog photo.

Anti-goals:

- Never hand Meta an image format it will not accept.
- Never let a retried send deliver the same photo or text twice.
- Never destroy a stored photo whose send outcome is unknown.
- Never expose another project's photo through the photo endpoint.
- Never show one of a customer's several photos and silently hide the rest.

## Constraints

- **Media bytes never pass through the API server.** Every existing upload (product photos, widget photos,
  test-chat photos, KB documents) is a presigned browser PUT to R2. This change follows that pattern; the new send
  endpoint receives only the R2 key.
- **Meta accepts JPEG, PNG and GIF for `attachment.payload.url`, and not WebP.** WhatsApp's Cloud API does accept
  static WebP, but Messenger/Instagram do not document it. `media_store._store` currently writes a **WebP** display
  variant and deletes the original, so the customer-photo path cannot be reused as-is for an outbound photo. The
  outbound variant must be JPEG. (Verified against Meta's Send API documentation during review; the exact error a
  rejected format produces was not reproduced against the live API.)
- **Meta fetches the image by URL.** Messenger/Instagram take
  `attachment: {type: "image", payload: {url}}`; WhatsApp Cloud takes `{"type": "image", "image": {"link": url}}`.
  A short-lived presigned R2 GET URL (`core.storage.presign_get`, 300s) is what we hand over — the same mechanism
  `ProductImageFileView` already relies on.
- **No caption field.** WhatsApp image messages support `caption`; Messenger attachments do not. To keep one send
  path, a photo and its text are sent as two ordered parts (photo first, then text).
- **24-hour messaging window.** Facebook, Instagram, and WhatsApp only accept free-form messages within 24h of the
  customer's last inbound message. The existing text staff-reply enforces this; the photo send must enforce the same
  check, including when the conversation has no inbound message at all.
- **Upload prefix ownership.** `media.is_own_upload(prefix, key)` already pins an upload to one conversation.
- **Staff sends are inline, like text staff replies.** `ConversationStaffReplyView` calls `deliver()` synchronously so
  the person at the keyboard learns immediately whether the message went out. The photo send does the same. It
  therefore does not use `attempt_delivery`'s background retry, and the row lifecycle differs from the bot's (see
  "Unknown outcomes" below).
- Photos are stored under `chat_media/<project slug>/`, the same place customer photos go.

## Approaches

| Approach | What it does | Verdict |
|---|---|---|
| **A. Browser PUT to R2, send a presigned URL to Meta** | Reuses `presign_chat_photo`, `uploaded_photo_ok`, `presign_get`, and the `_send_parts` bookkeeping. Adds a JPEG outbound variant. | **Picked.** Smallest change that works on all three channels, and it adds no new upload machinery. |
| B. Download the file to Django, then multipart-upload to each channel | WhatsApp needs an upload step anyway (media id); Messenger accepts a `filedata` upload too. | Rejected. Photo bytes would flow through the API server, needs a per-channel media-id handshake, and duplicates what presign already does. |
| C. Send the photo as a link in the text | Zero delivery work. | Rejected. The customer gets a URL, not an image — not what "send a product photo" means. |

## Design

### Storage: a JPEG variant for outbound photos

Customer photos (inbound) keep today's behavior: `media_store._store` writes a WebP display + WebP thumb, which is
correct because only a browser ever reads them.

Staff photos (outbound) need a Meta-acceptable format. `agents/image_search.py` gains a JPEG sibling of `_webp`:

```python
def _jpeg(img: Image.Image, edge: int) -> bytes      # like _webp, saved as JPEG quality 85
def make_send_variant(data: bytes) -> bytes          # load_image then _jpeg(DISPLAY_EDGE)
```

`media_store.py` gains `store_outbound_photo(conversation, att) -> str`:

- reads the upload through the existing `read_stored(att["path"])` (which enforces the size cap and the allowed
  `tmp/` prefixes), so a bad upload raises `MediaError` as it does today;
- writes `chat_media/<slug>/<uuid>.jpg` (the send + display copy) and `chat_media/<slug>/<uuid>_thumb.webp`
  (`thumb_key` derives that name from the display key, as it already does);
- deletes the `tmp/` upload;
- returns the display key.

It takes the **conversation**, not a `Message`: it needs `project.slug`, and it runs before the message row exists so
that a storage failure leaves no row behind. `_store` stays untouched for the inbound path.

Reusing `load_image` also means the stored copy is upright (EXIF applied) and metadata-free, which the browser's
`downscaleImage` only guarantees when it actually re-encoded the file.

### Upload and send (new endpoints)

Two views beside the existing test-chat pair in `channels_app/api.py`, registered in `urls_api.py`:

- `POST /api/channels/conversations/<uuid>/staff-reply/photo/presign`
  `{content_type, size}` → `{key, url, headers}` via `media.presign_chat_photo` under the conversation's
  `tmp/chat/<slug>/<conversation id>/` prefix. Rejects an unsupported type or an oversize body with the existing
  `invalid_image` / `PHOTO_ERROR` response.
- `POST /api/channels/conversations/<uuid>/staff-reply/photo`
  `{key, text?}` → the created outbound message.

The send view does, in order:

1. `_get_conversation` (existing access check), then the **same** 24-hour window check the text reply uses. That
   check is currently inline in `ConversationStaffReplyView.post`; extract it to one helper and call it from both.
2. `is_own_upload(prefix, key)` and `uploaded_photo_ok(key)` — the upload must be this conversation's, present, and
   within the size cap.
3. `store_outbound_photo(conversation, att)` — an `invalid_image` 400 on `MediaError`/`NotAnImage`, before any row
   exists.
4. `create_outbound_message(..., attachment_info=[{"type": "image", "path": new_key}], sender="staff")`, which gains
   an `attachment_info` parameter (and sets `has_attachments` from it).
5. `deliver(conversation, msg, last_in)` — unchanged call, with the outcome handling below.
6. `note_staff_activity` and `emit_event("message.staff_reply", ...)` on success, plus `schedule_memory_update` and
   the `draft_knowledge_suggestion` on-commit hook the text path already runs.

Text is optional: a photo with no text sends only the photo.

### Unknown outcomes (staff send)

`deliver()` returns False both when a send definitely failed and when its outcome is unknown (a timeout or 5xx —
Meta may have accepted it; `_send_parts` leaves `meta["inflight"]` set for exactly this case). Treating both as
"failed" and deleting the stored objects would destroy the only copy of a photo the customer may already have.

So on a False return:

- `"inflight" in msg.meta` → the outcome is unknown. Set `status = "unknown"` (an existing status, meaning "a send
  may or may not have been delivered; needs resolving"), **keep the stored objects**, and return 502 with a message
  saying the photo may or may not have gone through. Do not delete anything.
- otherwise → a definite failure. Set `status = "failed"`, delete the display key and its thumb, and return the
  existing `send_failed` 502.

A staff send carries no `biz_opaque_callback_data` and so is not resolved by the WhatsApp status webhook; unlike the
bot path there is no automatic resolver. `status = "unknown"` is left visible in the thread for a human to act on.
`shortcut: an unknown staff send is never auto-resolved, unlike the bot's. Add it to a resolver (or send
biz_opaque_callback_data) if unresolved staff sends start piling up.`

### Delivery (one new part type)

`_send_parts` currently takes `parts: list[str]` and calls `send(text, ref)`. It becomes `parts: list[str | dict]`,
where a `str` is text and `{"image": <r2 key>}` is a photo. The per-part reference, `parts_sent`, `inflight`
handling, and the "unknown outcome" hold all stay exactly as they are — so a retry after the image part succeeded
resumes at the text part and resends only the text.

To keep the image/text branch directly testable, `_send_facebook` and `_send_whatsapp` each build their closure in a
named helper (`_facebook_send(conversation, out_msg)`, `_whatsapp_send(conversation, out_msg)`); the part is presigned
inside the closure so every retry gets a fresh 300s URL.

- Messenger/Instagram: `{"recipient": ..., "message": {"attachment": {"type": "image", "payload": {"url": presign_get(key)}}}}`.
- WhatsApp: `{"messaging_product": "whatsapp", "to": ..., "type": "image", "image": {"link": presign_get(key)}}`.

Both keep the existing failure contract (`{}` definite, `{"unknown": True}` when it may have landed) by reusing the
same `never_sent`/status-code logic as the text senders. The `self` and `widget` senders stay no-ops.

The staff send passes `parts = [{"image": key}] + split_text(text)`; the bot path is untouched and still passes
strings only.

### Showing photos in the dashboard

A customer message can carry several photos (`attachment_info` is a list, and `image_reply` already numbers multiple
photos per turn). So the API exposes a **list**, not one URL.

- New `GET /api/channels/messages/<uuid>/photo?index=<n>&size=thumb` — access-checks the message's project, 404s when
  that index has no stored photo path, and otherwise `HttpResponseRedirect(presign_get(key))` (or `thumb_key(key)` for
  `size=thumb`), exactly like `ProductImageFileView`. One auth-checked entry point rather than exposing R2 keys.
- `_message_dict` gains `"photo_urls": list[str]` — one `/api/channels/messages/<id>/photo?index=i` per image
  attachment that has a stored path. A photo whose storage failed has no path, contributes no URL, and keeps rendering
  as "📷 Photo".
- `MessageList` renders each URL in `photo_urls` as a lazily-loaded `<img>` (max height ~240px, rounded, links to the
  full-size variant in a new tab); "📷 Photo" is shown only when `hasPhoto` is true and `photo_urls` is empty.

### Showing them live, not after a reload

Customer photos are stored asynchronously by `store_message_media`, *after* the row exists. The first
`getMessages` response for an inbound photo therefore has `has_photo: true` and no `photo_urls`, and today's 5-second
poll only appends messages it has not seen (`conversations/[id]/page.tsx`, the polling effect) while
`refreshPhotoMatches` merges only `credits` and `photoMatches`. A photo would appear only on a full page reload.

So the poll must also merge refreshed fields by id, not just append new messages: `photo_urls` (and the existing
`credits` / `photo_matches`) are taken from the fresh response for every message already on screen. The
`refreshPhotoMatches` merge gains `photoUrls` for the same reason.

### Frontend composer

`MessageComposer` already has the file input, downscale, and `onSendPhoto` wiring, gated to test chats with image
search on. Changes:

- Show the photo button in **staff mode** as well, for every channel. In test-chat mode keep the existing
  `image_search_on` gate so current test-chat behavior is unchanged.
- The staff-mode handler calls the two new endpoints (downscale via `upload.downscaleImage`, PUT via `upload.putToR2`,
  then the send) and appends the returned message to the thread.
- `api.ts`: add `staffPhotoPresign` / `staffPhotoSend` alongside `staffReply`, and `photo_urls` to
  `ConversationMessage`.

## Risks and open questions

- **Instagram URL images.** Instagram messaging is stricter than Messenger about image attachments in some cases. The
  same `payload.url` form is used for both because they share `/me/messages`; if Instagram rejects it in practice,
  that channel needs its own upload path. Called out rather than solved now.
- **The WebP finding rests on Meta's documentation**, not on a reproduced live failure. If Messenger turns out to
  accept WebP, the JPEG variant is harmless but unnecessary.
- **Widget conversations** receive the photo as a stored message; whether the visitor iframe renders it is not part
  of this change.
- The 24-hour-window helper extraction touches the existing text staff-reply path; existing tests for that path must
  keep passing.

## Tests

New `backend/channels_app/tests/test_staff_photo.py`:

- presign: rejects a bad content type and an oversize body; issues a key under this conversation's prefix only.
- send: rejects another conversation's key, a missing object, and a conversation the user has no access to.
- send: refuses outside the 24-hour window with `outside_messaging_window`, and refuses when there is no inbound
  message at all.
- send: creates an outbound row with `sender="staff"`, an attachment path under `chat_media/` ending in `.jpg`, and
  `has_attachments`.
- send: a storage failure returns 400 and leaves no message row.
- send: a definite delivery failure marks the row `failed`, returns 502, and deletes the stored objects.
- send: an unknown outcome marks the row `unknown`, returns 502, and **keeps** the stored objects.
- delivery: parts are `[image, text...]` in that order; a resumed send after `parts_sent=1` sends only the text.
- delivery: Messenger and WhatsApp image payloads carry a presigned URL (request bodies asserted).
- photo endpoint: 302 with a presigned URL for a stored photo, the thumb variant for `size=thumb`, 404 for an index
  with no photo, 404 for another project's message.
- `_message_dict`: `photo_urls` lists every stored photo and is empty for an unstored one.
