# Public Meta Integration: Messenger & Instagram

Status as of 2026-11-05. App: **AutoMagic Pro**, App ID `1257774078107684`.

## 1. The goal in plain words

Today only your own Pages can send messages to the app. A public integration lets any business click **Connect**, pick their Page, and have their customers' messages arrive at your webhook.

Three things must be true:

- **Meta trusts you.** Your app passes App Review, so people outside your team can grant it access to their Pages.
- **The user says yes.** They log in with Facebook and approve permissions. You receive a Page access token for each Page they choose.
- **Each Page is switched on.** You subscribe the Page to your app, so Meta forwards its messages to your webhook.

## 2. The steps, explained simply

1. **Set up the app for strangers.** Add the Facebook Login product, a privacy policy, a terms page, a data-deletion URL, and an OAuth redirect URI. Verify your business. Meta will not review an app without these.
2. **Ask for the right permissions.** A permission is a "may I?" that the user approves at login. Without App Review approval, only people with a role on your app (admin, developer, tester) can grant it.
   - `pages_show_list`: see which Pages the user manages.
   - `pages_manage_metadata`: subscribe a Page to your webhooks.
   - `pages_messaging`: send and receive Messenger messages for the Page.
   - `pages_read_engagement`: read basic Page and user profile info (names).
   - `instagram_basic`: read the linked Instagram account.
   - `instagram_manage_messages`: send and receive Instagram DMs.
3. **Pass App Review.** For each permission you explain the use case, record a screencast of the login and messaging flow, and complete the Data Use Checkup. `pages_messaging` also needs an API pre-check and a test Page.
4. **Log the user in.** Redirect them to the Facebook Login dialog with a `state` value you can verify on return.
5. **Exchange the code for tokens.** The callback receives a `code`. Exchange it (server-side, with your app secret) for a short-lived user token, then for a long-lived user token.
6. **List their Pages.** Call `GET /me/accounts`. Each Page comes back with its own **Page access token**. Page tokens made from a long-lived user token do not expire, but they stop working if the user removes your app or loses their role.
7. **Find the Instagram account.** Call `GET /{page-id}?fields=instagram_business_account`. The account must be Professional and linked to the Page.
8. **Save the connection.** Store the Page ID, Instagram account ID and Page token against the user's project. Encrypt the token.
9. **Subscribe the Page.** Call `POST /{page-id}/subscribed_apps?subscribed_fields=messages,messaging_postbacks&access_token={page-token}`. This is the step that makes Meta send that Page's events to your app. Do it again if the user reconnects.
10. **Receive events.** One webhook URL serves everyone. Check the `X-Hub-Signature-256` header with your app secret, then look up the connection by the Page ID or Instagram account ID in the payload.
11. **Handle disconnects.** If the user removes your app, Meta calls your deauthorize callback. Mark the connection inactive and delete the token. Also provide a data-deletion URL.

## 3. Where the codebase is today

What already works:

- **One webhook for all tenants.** `backend/channels_app/views_facebook.py` serves Messenger, Instagram and WhatsApp at one URL. It verifies `X-Hub-Signature-256` against `settings.META_APP_SECRET`.
- **Routing by Page.** `handle_facebook_message` (`backend/channels_app/service/facebook_messing.py`) reads the recipient ID and calls `meta_credentials()` in `models.py`. That matches `credentials.page_id` or `credentials.instagram_account_id` to a `ChannelCredential`, which belongs to one project. This is the multi-tenant part, and it is already done.
- **Sending.** `facebook_client.py` sends replies with the stored Page token.
- **Legal pages.** The frontend has `/privacy`, `/terms`, `/dpa` and `/data-deletion` (`frontend/app/(legal)`). The data-deletion page is already written as the Meta "Data Deletion Instructions URL".

What is manual today:

- **No Facebook Login.** In `frontend/app/(dashboard)/channels/page.tsx` the user pastes `page_id`, `page_access_token` and a `verify_token` by hand. The same applies to `instagram_account_id`.
- **Manual webhook setup.** The UI tells each user to paste the callback URL into their own Meta App Dashboard. That only works for people who own a Meta app.
- **No Page subscription call.** Nothing in the code calls `/{page-id}/subscribed_apps`.
- **No deauthorize callback.** There is no endpoint that Meta can call when a user removes the app.
- **Tokens are stored unencrypted** in `ChannelCredential.credentials` (a JSON field). I found no encryption in the backend.
- **Webhook verification is per channel.** `handle_facebook_webhook_verification` finds a channel by its `verify_token`. A public app has one webhook, so it needs one global verify token.

## 4. What we still need to build

| # | Work | Where |
|---|------|-------|
| 1 | Add `META_APP_ID`, `META_VERIFY_TOKEN` and the OAuth redirect URI to settings. `META_APP_SECRET` already exists. | backend settings |
| 2 | `GET` endpoint that builds the Facebook Login URL with a signed `state` containing the project. | `channels_app` |
| 3 | Callback endpoint: check `state`, exchange the code, get the long-lived token, call `/me/accounts`, return the Page list. | `channels_app` |
| 4 | "Select Pages" endpoint: create a `ChannelCredential` per chosen Page (`facebook`, plus `instagram` when a linked account exists) and call `subscribed_apps`. | `channels_app` |
| 5 | Disconnect: call `DELETE /{page-id}/subscribed_apps`, then deactivate the credential. | `channels_app` |
| 6 | Deauthorize callback (parses Meta's `signed_request`) that deactivates the user's connections. | `channels_app` |
| 7 | Encrypt Page tokens at rest. | `models.py` |
| 8 | Use one global verify token for the app-level webhook. | `service/facebook_messing.py` |
| 9 | Add a **Connect with Facebook** button and a Page picker. Keep manual paste as a fallback. | `channels/page.tsx` |

## 5. Analysis of App ID 1257774078107684

Read through the Meta Social Technologies MCP on 2026-11-05.

**Overall:** the webhook side is ready, but the app is not ready for public use. No messaging permission is approved, business verification fails, and Facebook Login has no redirect URI.

### Permissions

| Permission | Needed for | Status |
|------------|-----------|--------|
| `public_profile` | Login | Live, standard access |
| `email` | Login | Live, standard access |
| `openid` | Login | Live, advanced access |
| `pages_show_list` | List the user's Pages | **Missing.** Use case, screencast and Data Use Checkup not done |
| `pages_manage_metadata` | Subscribe Pages to webhooks | **Missing.** Same three steps not done |
| `pages_messaging` | Send and receive Messenger messages | **Missing.** Also needs API pre-check and test Page |
| `pages_read_engagement` | Read basic Page and user info | **Missing.** Same three steps not done |
| `instagram_basic` | Read the linked Instagram account | **Missing, and not in the review request list** |
| `instagram_manage_messages` | Instagram DMs | **Missing, and not in the review request list** |
| `pages_utility_messaging` | Utility-type messages | In the request list, not approved. Check whether you need it for plain replies |
| `business_management` | Pages owned through Business Manager | In the request list, not approved. Optional, and it also needs business verification |

### App settings

| Setting | Current value | Action |
|---------|--------------|--------|
| App mode | Live | OK |
| App type | Other (not a use-case app) | Add products separately: Facebook Login, Messenger, Instagram |
| Business verification | Fails | Complete it. App Review requires it |
| Contact email | Not verified | Verify it |
| Privacy policy URL | A freeprivacypolicy.com link | Replace with your own `/privacy` page |
| Terms of service URL | Empty | Set to your `/terms` page |
| Data deletion URL | Empty | Set to your `/data-deletion` page |
| Support URL, description, base domains | Empty | Fill in |
| OAuth redirect URIs | Empty | Add your callback URL, or login cannot work |
| Deauthorize callback URL | Empty | Add after building item 6 above |
| Compliance | Compliant, no open violations | OK |

### Webhooks

| Topic | Callback host | Fields | Note |
|-------|--------------|--------|------|
| `page` | `api.tumaragent.com` | messages, messaging_postbacks, message_edits, messaging_optins and others | OK |
| `instagram` | `api.tumaragent.com` | messages, messaging_postbacks | OK |
| `user` | an ngrok URL | none | Dev leftover. Remove or repoint |
| `whatsapp_business_account` | an ngrok URL | messages | Dev leftover. Repoint before real WhatsApp customers |

### App Review state

- Submission status is `UNSUBMITTED`, but the requirements check says "cannot submit while a previous submission is in review". These disagree. Open the App Review page in the dashboard to see which is right.
- The MCP could not list use cases because the app type is "Other".

## 6. Action checklist

1. Fix app settings: terms URL, data-deletion URL, own privacy URL, support URL, description, verified contact email.
2. Complete business verification.
3. Add the Facebook Login product and an OAuth redirect URI. Confirm the Instagram product is added.
4. Add `instagram_basic` and `instagram_manage_messages` to the review request.
5. Build items 1 to 9 from section 4. Review needs a working flow to record.
6. Test with a test user and test Page. Complete the `pages_messaging` API pre-check and test Page step.
7. Record screencasts and write use-case text for each permission. Complete the Data Use Checkup.
8. Submit for App Review, then switch the permissions to Advanced Access once approved.
9. Remove or repoint the ngrok webhook subscriptions.

## 7. Checked against Meta's docs (via the Meta MCP, 2026-11-05)

- **Subscribe call confirmed.** `POST /<PAGE_ID>/subscribed_apps?subscribed_fields=messages&access_token=<PAGE_ACCESS_TOKEN>`. It needs `pages_messaging` and `pages_manage_metadata`, and the Page token must come from someone with the MODERATE task on the Page. `GET` on the same edge shows which apps a Page has installed. ([Messenger webhooks](https://developers.facebook.com/documentation/business-messaging/messenger-platform/webhooks))
- **Instagram via Facebook Login confirmed.** The login grants `instagram_basic`, `instagram_manage_messages` and `pages_manage_metadata`. The `messages` webhook field also lists `pages_read_engagement` and `pages_show_list`. ([Instagram webhook fields](https://developers.facebook.com/documentation/instagram-platform/webhooks/fields))
- **`pages_messaging` depends on** `pages_manage_metadata` and `pages_show_list`. `pages_read_engagement` depends on `pages_show_list`.
- **Alternative for Instagram only: Instagram Login.** It uses `instagram_business_basic` and `instagram_business_manage_messages` and needs no Facebook Page. Your code routes by Page ID today, so Facebook Login is the smaller change. Pick this only if customers have Instagram accounts with no Page.
- **API version.** Your code calls Graph `v21.0` (`facebook_client.py`). The MCP reports `v27.0` as the latest and lists no deprecations for the app. Use one version constant in the new code.

## 8. Names and profile pictures

**Facebook (Messenger).** The User Profile API (`GET /{PSID}?fields=first_name,last_name,profile_pic`) needs **Advanced Access for the "Business Asset User Profile Access" feature**. That feature is not on the app today, so the call returns an empty object for anyone without a role on the app. `pages_messaging` is needed as well.

- `profile_pic` URLs expire. Download and store the image.
- The person must have messaged the Page first.
- Accounts created with a phone number return error `2018218` ("No profile available").
- `locale`, `timezone` and `gender` need `pages_user_locale`, `pages_user_timezone` and `pages_user_gender`. Request them under Page Settings → Advanced Messaging → Info About People only if you need them.

**Instagram.** Names and pictures need `instagram_manage_messages` with Advanced Access. Fields are `name`, `username` and `profile_pic`. Many accounts have no `name`. Picture URLs also expire. I have not confirmed these Instagram fields against Meta's docs.

**Code change needed.** `get_user_name()` in `backend/channels_app/facebook_client.py` requests `name` only and drops the picture. It should request `first_name,last_name,profile_pic` on Facebook and `name,username,profile_pic` on Instagram, then save the picture. Not done yet.

## Not checked

- Whether the Facebook Login and Instagram products are added. The MCP could not list products for this app type.
- Review steps and access levels change over time. Confirm against the App Dashboard before submitting.
