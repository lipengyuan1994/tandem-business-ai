# org (M02) — module card

Spec: [docs/roadmap/modules/M02-org.md](../../../../docs/roadmap/modules/M02-org.md). Status: complete for MVP (P2-01).

- Tables: `businesses` (row-level security on `id`), function `public_business_by_slug(slug)` (security definer).
- Registers: public business resolver (`registration.resolve_public_business`).
- Public (`public.py`): `get_business_brief`, `get_settings`, `get_sales_tax_rate`, `get_default_location`, `get_opening_hours`, `create_business` (bootstrap only).
- Tables also: `locations`, `opening_hours`, `business_settings`.

- `business_settings.sales_tax_rate`: exact numeric(6,4), default 0, range 0–1; included in the existing
  versioned settings API, audit and settings-changed events. Other settings remain in the JSON column.

- `get_public_email(db, ctx)` returns `public_profile.email` for notification Reply-To.

- P8-23: settings include a whitelisted plain-text `terminology` map; the public profile adds `avoid_medical_details` (boolean only).

P10-A0: typed setting `ai_autonomy_paused=False`; public `autonomy_paused(db, ctx, *, lock=False)`
reads it (optional share lock), `set_autonomy_paused(db, ctx, paused)` requires `org.settings.write`,
locks the row and changes only this key, with audit and `org.settings_changed`.

P10-A1: typed `outbound_send_delay_seconds=120` (integer 0–900) uses the existing versioned settings
API, audit and settings-changed event. Applies only to executor mail from assistant-origin proposals.

P10-C1: BusinessSettings.handoff_after_hours_text (1–1,000 chars) customizes the deterministic closed-hours
hand-over message. PublicProfileOut.ai_greeting discloses the business's AI assistant; no model generates it.

P10-B2: `checkin_delay_days=2` (zero off), quiet start/end hours (20/8), and care tone. Public profile supports validated optional review_url/booking_url; `customer_care_profile` exposes only these links and avoid_medical_details.
