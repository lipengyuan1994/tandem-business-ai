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
