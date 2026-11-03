# Add a Facebook Page to AutoMagic Pro (API only)

How to connect a new Facebook Page to app **AutoMagic Pro** (`1257774078107684`) so its
Messenger messages trigger our webhook, using only HTTP calls.

Related: [facebook-webhook-debugging.md](facebook-webhook-debugging.md)

---

## Can it be 100% API?

**Almost.** Meta has no API that creates a user token from a username and password.
A human has to grant consent **once**, by clicking "Continue" in the Facebook Login
dialog. Every other step is a plain HTTP call.

| Path | Human step | Token lifetime | Use when |
|---|---|---|---|
| **A. Page admin OAuth** | One consent click per admin | Page token never expires (from a long-lived user token). Dies if the admin's password changes or they lose page access | Onboarding a customer's page |
| **B. Business system user** | One-time Business Manager setup | Never expires, not tied to a person | Our own pages, or pages shared to our Business |

The app-level webhook (callback URL, verify token, fields under App Dashboard → Webhooks
→ Page) is **shared by every page** and is already set up. Each new page needs only:

1. its own **page → app subscription** (`POST /{page_id}/subscribed_apps`)
2. its own **`ChannelCredential`** in our backend (`page_id` + `page_access_token`)

Skip 1 and Meta never sends anything. Skip 2 and our webhook drops the event with
`ChannelCredential not found for page_id`.

---

## Prerequisites

- The person granting consent is an **admin** of the page, or has full control of it
  through Business Manager.
- App is in **Live mode**. With only **Standard Access** to `pages_messaging` and
  `pages_manage_metadata`, everything works only for pages managed by people who have a
  role on the app (admin, developer or tester), and only for messages from people with a
  role. Pages owned by outside customers, and messages from real customers, need
  **Advanced Access** (App Review).
- Path A: the **Facebook Login** product is added to the app, and your `REDIRECT_URI` is
  listed under Facebook Login → Settings → *Valid OAuth Redirect URIs*.

Variables used below:

```bash
GRAPH=https://graph.facebook.com/v21.0
APP_ID=1257774078107684
APP_SECRET=...            # .env META_APP_SECRET. Never commit, never put in frontend
REDIRECT_URI=https://<your-ngrok-or-domain>/fb/callback
SCOPES=pages_show_list,pages_messaging,pages_manage_metadata
```

---

## Path A: page admin OAuth

### A1. Send the admin to the Login dialog (the one human step)

Open this URL in the admin's browser:

```
https://www.facebook.com/v21.0/dialog/oauth?client_id=1257774078107684&redirect_uri=<REDIRECT_URI>&scope=pages_show_list,pages_messaging,pages_manage_metadata&response_type=code&state=<random>
```

The admin picks the page(s) and clicks Continue. Facebook then redirects to:

```
<REDIRECT_URI>?code=<CODE>&state=<random>
```

Check that `state` matches what you sent. The `code` works once and expires in about 10 minutes.

> Shortcut for our own testing: Graph API Explorer → app *AutoMagic Pro* → add the 3
> permissions → Generate Access Token gives a short-lived user token. Then skip to A3.

### A2. Exchange the code for a user token

```bash
curl -G "$GRAPH/oauth/access_token" \
  --data-urlencode "client_id=$APP_ID" \
  --data-urlencode "client_secret=$APP_SECRET" \
  --data-urlencode "redirect_uri=$REDIRECT_URI" \
  --data-urlencode "code=<CODE>"
# → {"access_token":"<SHORT_USER_TOKEN>","token_type":"bearer","expires_in":...}
```

`redirect_uri` must match A1 exactly.

### A3. Swap for a long-lived user token (about 60 days)

```bash
curl -G "$GRAPH/oauth/access_token" \
  -d grant_type=fb_exchange_token \
  -d client_id=$APP_ID \
  -d client_secret=$APP_SECRET \
  -d fb_exchange_token=<SHORT_USER_TOKEN>
# → {"access_token":"<LONG_USER_TOKEN>", ...}
```

Do this before A4. A page token obtained from a long-lived user token **does not
expire**. One obtained from a short-lived user token dies in about an hour.

### A4. List the admin's pages and get page tokens

```bash
curl -G "$GRAPH/me/accounts" \
  -d fields=id,name,access_token,tasks \
  -d access_token=<LONG_USER_TOKEN>
# → {"data":[{"id":"<PAGE_ID>","name":"My Page","access_token":"<PAGE_ACCESS_TOKEN>","tasks":["MANAGE","MESSAGING",...]}]}
```

`tasks` must include `MESSAGING`, and `MANAGE` is needed to subscribe. If the page is
missing from the list, the admin didn't select it in A1. Redo A1 and pick it.

Continue at **Common steps**.

---

## Path B: Business system user (no person involved after setup)

One-time setup in Business Manager (business.facebook.com). This part is UI only:
1. Business Settings → Apps → add **AutoMagic Pro** (owned or shared).
2. Business Settings → Users → System users → create an **Admin** system user and note
   its `SYSTEM_USER_ID`.
3. Get one token with business management rights. Either generate it for the system
   user right there (scopes: `business_management`, `pages_show_list`,
   `pages_messaging`, `pages_manage_metadata`), or use a business admin's user token.
   Call it `ADMIN_TOKEN`.

The rest is API.

`appsecret_proof` is HMAC-SHA256 of the token, keyed with the app secret. Some
system-user calls require it:

```bash
proof() { printf '%s' "$1" | openssl dgst -sha256 -hmac "$APP_SECRET" | sed 's/^.* //'; }
```

### B1. Make sure the page belongs to the business

Our own page, claimed by the business:

```bash
curl -X POST "$GRAPH/<BUSINESS_ID>/owned_pages" \
  -d page_id=<PAGE_ID> -d access_token=$ADMIN_TOKEN
```

Or a customer's page (the customer approves the request on their side):

```bash
curl -X POST "$GRAPH/<BUSINESS_ID>/client_pages" \
  -d page_id=<PAGE_ID> -d permitted_tasks='["MANAGE","MESSAGING"]' -d access_token=$ADMIN_TOKEN
```

### B2. Give the system user access to the page

```bash
curl -X POST "$GRAPH/<PAGE_ID>/assigned_users" \
  -d user=<SYSTEM_USER_ID> \
  -d tasks='["MANAGE","MESSAGING"]' \
  -d business=<BUSINESS_ID> \
  -d access_token=$ADMIN_TOKEN
# → {"success":true}
```

### B3. Generate a system user token (never expires)

```bash
curl -X POST "$GRAPH/<SYSTEM_USER_ID>/access_tokens" \
  -d business_app=$APP_ID \
  -d scope=$SCOPES \
  -d appsecret_proof=$(proof "$ADMIN_TOKEN") \
  -d access_token=$ADMIN_TOKEN
# → {"access_token":"<SYSTEM_USER_TOKEN>"}
```

You can skip this if `ADMIN_TOKEN` is already the system user's token with these scopes.

### B4. Get the page token

```bash
curl -G "$GRAPH/me/accounts" \
  -d fields=id,name,access_token,tasks \
  -d access_token=<SYSTEM_USER_TOKEN>
# → data[].id = PAGE_ID, data[].access_token = PAGE_ACCESS_TOKEN
```

Continue at **Common steps**.

---

## Common steps (both paths)

### C1. Check the page token

```bash
curl -G "$GRAPH/debug_token" \
  -d input_token=<PAGE_ACCESS_TOKEN> \
  -d access_token="$APP_ID|$APP_SECRET"
```

Expect: `app_id` = `1257774078107684`, `type` = `PAGE`, `is_valid` = `true`,
`expires_at` = `0` (never expires), and `scopes` containing `pages_messaging` and
`pages_manage_metadata`.

### C2. Subscribe the page to the app (this makes the webhook fire)

```bash
curl -X POST "$GRAPH/<PAGE_ID>/subscribed_apps" \
  -d subscribed_fields=messages,messaging_postbacks,message_edits,messaging_optins \
  -d access_token=<PAGE_ACCESS_TOKEN>
# → {"success":true}
```

Only use fields that are also enabled on the app's Page webhook in the dashboard
(currently: `messages`, `messaging_postbacks`, `message_edits`, `messaging_optins`,
`messaging_customer_information`, `name`).

Confirm:

```bash
curl -G "$GRAPH/<PAGE_ID>/subscribed_apps" -d access_token=<PAGE_ACCESS_TOKEN>
# → {"data":[{"id":"1257774078107684","name":"AutoMagic Pro","subscribed_fields":[...]}]}
```

### C3. Register the page in our backend

Our API uses Django session auth with CSRF, so keep a cookie jar:

```bash
API=http://localhost:8251
JAR=/tmp/amp.cookies

# get the CSRF cookie
curl -s -c $JAR "$API/api/auth/csrf/" > /dev/null
CSRF=$(awk '/csrftoken/ {print $7}' $JAR)

# log in
curl -s -b $JAR -c $JAR -X POST "$API/api/auth/login/" \
  -H "Content-Type: application/json" -H "X-CSRFToken: $CSRF" -H "Referer: $API" \
  -d '{"email":"<you@example.com>","password":"<password>"}'
CSRF=$(awk '/csrftoken/ {print $7}' $JAR)   # CSRF token rotates after login

# create the Facebook channel for this page
curl -s -b $JAR -X POST "$API/api/channels/channels" \
  -H "Content-Type: application/json" -H "X-CSRFToken: $CSRF" -H "Referer: $API" \
  -d '{
        "project_slug": "<project-slug>",
        "name": "My Page Messenger",
        "channel": "facebook",
        "knowledge_base_id": "<kb-uuid>",
        "is_active": true,
        "credentials": {"page_id": "<PAGE_ID>", "page_access_token": "<PAGE_ACCESS_TOKEN>"}
      }'
```

To update the token on an existing channel:

```bash
curl -s -b $JAR -X PATCH "$API/api/channels/channels/<channel-uuid>" \
  -H "Content-Type: application/json" -H "X-CSRFToken: $CSRF" -H "Referer: $API" \
  -d '{"credentials": {"page_id": "<PAGE_ID>", "page_access_token": "<NEW_TOKEN>"}}'
```

Notes:
- `PATCH` **replaces** the whole `credentials` object, so always send both keys.
- `knowledge_base_id` is required in practice. Without it, inbound messages log
  `KnowledgeBase not configured` and get no reply.
- Only one active Facebook channel per `page_id`. Routing takes the first match.

### C4. Verify end to end

1. Send the page a message from Messenger, from a role account if you only have
   Standard Access.
2. ngrok inspector `http://127.0.0.1:4040` should show `POST /webhooks/meta/` → 200.
3. The conversation appears in the dashboard.

If nothing arrives, work through [facebook-webhook-debugging.md](facebook-webhook-debugging.md).

---

## Removing a page

```bash
curl -X DELETE "$GRAPH/<PAGE_ID>/subscribed_apps" -d access_token=<PAGE_ACCESS_TOKEN>
```

Then deactivate the channel: `PATCH /api/channels/channels/<id>` with `{"is_active": false}`.

---

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `(#200) ... permission(s) must be granted before impersonating a user's page` / `(#200) Permissions error` on `subscribed_apps` | Token lacks `pages_manage_metadata`, or the user has no `MANAGE` task on the page | Redo A1 with the scopes, or B2 with `MANAGE` |
| `(#190) Error validating access token: Session has expired` | Page token came from a short-lived user token | Do A3 before A4 |
| `(#190) ... user changed their password` / `has not authorized application` | Admin reset their password or removed the app | Redo Path A, or move to Path B |
| `Can't Load URL: The domain of this URL isn't included in the app's domains` | `REDIRECT_URI` isn't whitelisted | Add it to Facebook Login → Valid OAuth Redirect URIs |
| `(#100) ... subscribed_fields ... invalid` | Field isn't enabled on the app's Page webhook | Enable it in App Dashboard → Webhooks first |
| `subscribed_apps` OK, still no POST | Live mode + Standard Access + non-role sender, or a stale ngrok URL | See the debugging doc, steps 1 and 6 |
| POST arrives, logs `ChannelCredential not found for page_id` | Step C3 skipped, or `is_active=false` | Do C3 |
