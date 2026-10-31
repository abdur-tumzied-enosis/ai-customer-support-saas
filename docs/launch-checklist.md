# Launch checklist

Going-live work, not features. Assumptions: free beta at launch (no payments yet), customers worldwide including the EU (GDPR applies; we are the processor for our customers' chat data).

Legend: `[ ]` todo · `[x]` done · `[~]` needs a human (lawyer, ops, account setup)

## P0 — blockers

### Security
- [x] `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS` fail closed (prod refuses to boot on dev defaults)
- [x] CORS / CSRF trusted origins from env, not hardcoded localhost (+ `CSRF_COOKIE_DOMAIN` for app./api. split)
- [x] HTTPS hardening when `DEBUG=false`: SSL redirect behind proxy, HSTS, secure cookies
- [x] Rate-limit login / signup per IP (`AUTH_THROTTLE_RATE`); `NUM_PROXIES` so throttles see real client IPs
- [x] Admin moved off `/admin/` (env `ADMIN_URL`)
- [x] Qdrant: API key required, ports not published in prod compose
- [x] Untrack `backend/celerybeat-schedule`, gitignore it
- [x] Login no longer prints request bodies (plaintext passwords were going to logs)
- [x] `/health/` liveness endpoint (exempt from HTTPS redirect)
- [ ] Encrypt channel credentials + API tool secrets at rest (`ChannelCredential.credentials` is plaintext JSON today; privacy policy does not claim encryption until this lands)

### Legal pages (templates — `[~]` lawyer review before launch)
Company details are placeholders in `frontend/lib/site.ts` (fictional name, `.example` domain); swap there.
The privacy policy promises the widget AI disclosure (P1) and the 90-day retention job (P2): both must ship before launch.
- [x] `/privacy` — data collected, sub-processors (Google Gemini/Vertex AI, Meta, hosting), retention, rights, contact
- [x] `/terms` — terms of service incl. acceptable use and AI-output disclaimer
- [x] `/data-deletion` — page with instructions (Meta app review requires this URL)
- [x] `/dpa` — data processing agreement for business customers
- [x] Footer with legal links on landing, auth and legal pages
- [x] Sign-up consent checkbox; store `terms_accepted_at` + `terms_version`
- [x] Landing claims fixed ("thousands of businesses", "encrypted")
- [ ] `[~]` Name hosting + email providers in the sub-processor table
- [ ] Meta data-deletion callback endpoint (optional; the instructions URL satisfies app review)

### Broken / missing flows
- [ ] Password reset: backend endpoints + email (frontend page exists, backend does not)
- [ ] Email backend configured from env (SMTP / provider)
- [ ] Email verification on sign-up (stops token-burning fake accounts)

### Deployment
- [ ] Full production compose (Django, Celery workers + beat, Next.js, Postgres, Redis, Qdrant, model servers) behind a reverse proxy with TLS
- [ ] `[~]` Automated Postgres + Qdrant backups, restore tested once

## P1 — trust and UI polish
- [ ] Landing: footer, pricing (free beta), FAQ, contact, real product screenshot; claims match what ships
- [ ] Favicon, OG image, per-page metadata, `robots.txt`, `sitemap.xml`
- [ ] `not-found.tsx`, `error.tsx`, `loading.tsx`
- [ ] Toasts for success/error; helpful empty states
- [ ] First-run onboarding checklist: knowledge base → channel → widget → test message
- [x] Account settings (`/settings`): profile, change password, export data (JSON), delete account (GDPR art. 15/17/20)
  - delete purges solo projects incl. Qdrant collections + stored files; shared projects stay with teammates
  - [ ] email change (needs the email-verification flow)
- [x] Widget: AI disclosure ("You're chatting with an AI assistant", EU AI Act art. 50, not configurable) + privacy link (business's own `privacy_url`, else ours) + "Powered by" (`show_branding`)
  - prod env: `SITE_URL` (frontend origin) and `PRODUCT_NAME` on the backend

## P2 — operations (first week)
- [ ] Error tracking (Sentry) backend + frontend
- [ ] `/health` endpoint + `[~]` uptime monitor
- [ ] Enforce monthly token quota per plan
- [ ] Retention job: delete conversations older than N days (match privacy policy)
- [ ] `[~]` Support email + status page
- [ ] Privacy-friendly analytics (no cookie banner needed)

## Later (not for launch)
- Stripe billing, SOC 2, i18n of dashboard, CMS for legal pages
