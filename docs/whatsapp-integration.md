# WhatsApp integration (Cloud API)

Inbound and outbound text over the WhatsApp Cloud API, using the same reply pipeline as Messenger and Instagram.

## Meta setup (per WhatsApp number)

1. In a Meta app (the platform's app or the business's own), add the **WhatsApp** product.
2. In WhatsApp Manager, get the **Phone Number ID** and the **WhatsApp Business Account ID**. The test number works for development, but it only messages up to 5 recipient numbers you have verified.
3. Create a **System User** token with `whatsapp_business_messaging` and `whatsapp_business_management`. Temporary tokens expire after 24h.
4. Under App Dashboard → WhatsApp → Configuration → Webhook:
   - Callback URL: `https://<backend>/webhooks/facebook/` (the same endpoint as Messenger and Instagram)
   - Verify token: any string, entered on the channel too
   - Subscribe to the `messages` field.
5. Connect the channel in the dashboard (`POST /api/channels/channels`, `channel: "whatsapp"`) with `phone_number_id`, `business_account_id`, `access_token`, `verify_token`, `app_id`, `app_secret`, and a knowledge base.

## How it works

| Piece | Where |
|---|---|
| Routing: `object == "whatsapp_business_account"` | `views_facebook.facebook_webhook` |
| Signature: the platform `META_APP_SECRET` **or** the matching channel's `app_secret` (WhatsApp numbers can sit on another Meta app) | `views_facebook._app_secrets` |
| Inbound: credential found by `metadata.phone_number_id`; contact `external_id` = `wa_id`; name from `contacts[].profile.name` | `service/whatsapp_messaging.py` |
| Send: `POST /{phone_number_id}/messages`, text split at 4000 chars | `facebook_client.send_whatsapp_text`, `reply._send_whatsapp` |

- Only `type == "text"` gets a bot reply. Media, location, reactions and other types are stored with `has_attachments`.
- `statuses` webhooks (sent, delivered, read, failed) are ignored.
- **24h window**: free-form text only reaches a customer within 24h of their last message. After that, staff replies are refused (`outside_messaging_window`). A failed bot send stores Meta's error text in `meta.send_error` (e.g. 131047 "Re-engagement message").

## Not done yet

- Message templates, needed to message a customer after 24h.
- Embedded Signup, so businesses can connect their own numbers without pasting tokens. It needs Tech Provider status.
- Downloading media (`GET /{media_id}`) and marking messages as read.
