# Facebook Messenger Webhook — Setup & Debugging

App: **AutoMagic Pro** (`1257774078107684`) · Live mode
Webhook route: `POST/GET /webhooks/meta/` → `channels_app/views_facebook.py`
Local dev: `django` container on `localhost:8251`, exposed via ngrok.

---

## How delivery works (two subscriptions, both required)

```
Customer → Page ──(1)── App ──(2)── callback URL ── ngrok ── django:8251 /webhooks/meta/
```

1. **Page → App** (`/{page_id}/subscribed_apps`): the page must subscribe the app.
   Done **per page** with that page's access token. *Not* done by the App Dashboard.
2. **App → Callback** (App Dashboard → Webhooks → Page topic): callback URL, verify
   token and fields (`messages`, `messaging_postbacks`, ...). Done **once per app**.

Missing (1) is the classic silent failure: dashboard looks green, verification GETs
return 200, but no POST ever arrives.

Inbound POSTs are routed to a tenant by `entry[].messaging[].recipient.id` (the page
id) → `ChannelCredential(channel="facebook", is_active=True, credentials__page_id=...)`
(`channels_app/service/facebook_messing.py`). One `ChannelCredential` per page.

---

## Debug checklist (run in order)

### 1. Is the tunnel up and pointing at django?

```bash
curl -s http://127.0.0.1:4040/api/tunnels      # public_url + addr should be localhost:8251
```

Free ngrok URLs change on every restart → update the callback URL in
App Dashboard → Webhooks after restarting ngrok.

### 2. Did Meta hit us at all?

Open `http://127.0.0.1:4040` (ngrok inspector), or:

```bash
curl -s "http://127.0.0.1:4040/api/requests/http?limit=20" | python -c "import sys,json; [print(r['start'], r['request']['method'], r['request']['uri'], r['response']['status']) for r in json.load(sys.stdin)['requests']]"
```

| What you see | Meaning | Go to |
|---|---|---|
| Only `GET ...hub.mode=subscribe` → 200 | Callback OK, Meta isn't sending events | step 3 |
| `GET ...hub.mode=subscribe` → 403 | Verify token mismatch (`FB_VERIFY_TOKEN` in `.env` vs dashboard) | fix `.env` |
| `POST /webhooks/meta/` → 403 | Signature check failed — `META_APP_SECRET` ≠ app secret | fix `.env` |
| `POST /webhooks/meta/` → 200, no reply | Pipeline issue — check django/celery logs (`ChannelCredential not found for page_id`, `KnowledgeBase not configured`) | logs |
| Nothing at all | Tunnel/URL wrong | step 1 |

### 3. Is the page subscribed to the app?

```bash
docker compose exec -T django python manage.py shell -c "
import requests
from channels_app.models import ChannelCredential
for ch in ChannelCredential.objects.filter(channel='facebook'):
    c = ch.get_credentials()
    if not (c.page_id and c.page_access_token): print(ch.pk, 'not configured'); continue
    r = requests.get(f'https://graph.facebook.com/v21.0/{c.page_id}/subscribed_apps', params={'access_token': c.page_access_token})
    print(ch.pk, c.page_id, r.text)
"
```

- `{"data":[]}` → **page not subscribed** → step 4.
- Lists `AutoMagic Pro` with `subscribed_fields` → fine, go to step 5.

### 4. Subscribe the page to the app

```bash
curl -X POST "https://graph.facebook.com/v21.0/<PAGE_ID>/subscribed_apps" \
  -d "subscribed_fields=messages,messaging_postbacks,message_edits,messaging_optins" \
  -d "access_token=<PAGE_ACCESS_TOKEN>"
# → {"success":true}
```

Undo: same URL with `-X DELETE`.
Token needs `pages_manage_metadata` (+ `pages_messaging` to reply).

### 5. Is the page token valid and from this app?

```bash
curl "https://graph.facebook.com/v21.0/debug_token?input_token=<PAGE_ACCESS_TOKEN>&access_token=<PAGE_ACCESS_TOKEN>"
```

Expect `app_id: 1257774078107684`, `type: PAGE`, `is_valid: true`, scopes include
`pages_messaging`, `pages_manage_metadata`.

### 6. Who is sending the test message?

App is in **Live mode**. If `pages_messaging` has only *Standard Access*, Meta delivers
events only for people with a role on the app (admin/developer/tester) and only for
pages managed by those people. Test from a role account, or get **Advanced Access**
via App Review for real customers.

### 7. Fire a test event from Meta

App Dashboard → Webhooks → Page → `messages` → **Test**. Then re-check step 2.
(Delivery can lag a minute or two.)

---

## Adding another page

One app serves any number of pages. Full API-only walkthrough:
[facebook-add-page-via-api.md](facebook-add-page-via-api.md).

---

## Known pages

| Page ID | Subscribed | Notes |
|---|---|---|
| `1322204434316279` | 2026-10-15 | Was never subscribed → no events. Fixed via step 4. |
