# TumarAgent landing design

Scope: the public landing page (`frontend/app/page.tsx`). Tokens live in `.landing` in `frontend/app/globals.css`, so the dashboard keeps its own look.

## Direction
Warm and local, not a stock black-and-white SaaS template. Audience today: small Bangladeshi shops selling on Facebook and WhatsApp (plan: any market later). Bangla must look properly set, so the body font is a Bangla-capable face. Chosen by judgment; no UXKIN reference sites were used (UXKIN is not authenticated).

## Colour (HSL tokens)
| Role | Light | Dark |
|---|---|---|
| background (paper) | 40 38% 96% | 28 12% 7% |
| foreground (ink) | 28 20% 9% | 40 25% 94% |
| primary (bottle green) | 158 62% 18% | 152 48% 58% |
| muted-foreground | 30 10% 38% | 35 10% 66% |
| border | 38 18% 84% | 28 8% 20% |
| marigold (free-credit highlight only) | 42 96% 56% | 42 90% 58% |

Rules: one accent (green) for actions, marigold only for the free-credit offer. Never green text on green fill.

## Type
- Display (h1, h2): Fraunces.
- Body and Bangla: Hind Siliguri 400/500/600/700.
- Scale: hero 44/60px, section titles 30/40px, body 16-18px, small 14px.

## Shape and spacing
- Radius 0.5rem (existing `--radius`), cards 1rem for the chat sample.
- Sections: 80px vertical on desktop, 56px on phones; 24px side gutter.
- Shadow: none on cards, borders only.

## Rules
- No invented testimonials, logos, numbers or prices. The chat sample is labelled as an example.
- Free-credit facts (amount, deadline) come from `site.signupBonus` in `frontend/lib/site.ts`. The amount must match Billing > Pricing settings > signup credits in the admin.
- Logo: existing `components/layout/Logo.tsx` (from `app_logo/`), uses currentColor.
