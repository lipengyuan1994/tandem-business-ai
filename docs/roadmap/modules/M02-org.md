# M02 · org

| Field | Value |
|---|---|
| Release / phase | MVP · P2 |
| Tier / kind | B · PRE |
| Depends on | platform |
| Owns tables | `businesses`, `locations`, `opening_hours`, `business_settings` |

## Purpose
The business itself: profile, locations, opening hours, time zone, currency and typed settings used by other modules (hold minutes, self-approval, AI policy, retention). Creating a business happens only through `identity.bootstrap_business` (CLI) in MVP.

## Tables
`businesses` (no `business_id` column; its `id` is the tenant ID)

| Column | Type | Null | Notes |
|---|---|---|---|
| slug | text | no | unique, `^[a-z0-9-]{3,40}$`, used in public URLs |
| name | text | no | |
| legal_name | text | yes | |
| timezone | text | no | IANA, validated against `zoneinfo` |
| currency | char(3) | no | ISO 4217 |
| locale | text | no | e.g. `en-US` |
| country | char(2) | yes | |
| public_profile | jsonb | no | `{phone, email, address, website, about}`: shown on public pages |
| status | text | no | `active` \| `suspended` |

`locations` — `name`, `address jsonb`, `timezone` (defaults to business), `is_default bool` (exactly one default per business: partial unique index).

`opening_hours` — `location_id` (composite FK), `weekday smallint 0–6 (Mon=0)`, `opens time`, `closes time`. Multiple rows per day allowed (lunch breaks); `closes > opens`.

`business_settings` — one row per business: `settings jsonb` validated by the Pydantic model `BusinessSettings`:

| Key | Type | Default | Used by |
|---|---|---|---|
| `hold_minutes` | int 5–240 | 30 | scheduling |
| `slot_granularity_minutes` | 5/10/15/30 | 15 | scheduling |
| `booking_lead_time_minutes` | int | 60 | scheduling |
| `allow_self_approval` | bool | false | approvals |
| `always_review_action_types` | list[str] | `[]` | approvals (forces the proposal path) |
| `allow_backorders` | bool | false | inventory |
| `ai_policy` | `hosted_vultr` \| `hosted_any` \| `strict_private` | `hosted_vultr` | ai |
| `ai_daily_token_budget` | int | env default | ai |
| `retention_days_conversations` | int ≥ 30 | 730 | admin_ops |
| `public_chat_enabled` | bool | true | conversations |

## Business rules
1. A business's time zone change does not change stored UTC timestamps. The UI warns that future appointments keep their UTC instants.
2. Exactly one default location.
3. Settings writes are validated as a whole and versioned (`version` on the settings row).
4. Opening hours never cross midnight in MVP (split into two rows).

## Public interface (`public.py`)
```python
def get_business_brief(db, ctx) -> BusinessBrief                      # id, slug, name, timezone, currency, locale
def get_settings(db, ctx) -> BusinessSettings
def get_opening_hours(db, ctx, location_id: UUID | None = None) -> list[OpeningHoursDto]
def get_default_location(db, ctx) -> LocationDto
def create_business(db, *, name, slug, timezone, currency, locale) -> BusinessBrief   # used by identity.bootstrap only
def resolve_public_business(db, slug: str) -> PublicBusinessDto | None  # security-definer function wrapper
```

## API
| Method | Path | Permission |
|---|---|---|
| GET / PATCH | `/org/business` | `org.business.read` / `org.business.write` |
| GET / POST | `/org/locations` | `org.location.read` / `org.location.write` |
| PATCH | `/org/locations/{id}` | `org.location.write` |
| GET / PUT | `/org/locations/{id}/opening-hours` | `org.location.read` / `org.location.write` (replace whole week) |
| GET / PATCH | `/org/settings` | `org.settings.read` / `org.settings.write` (`ai_policy` also requires `ai.settings.write`) |
| GET | `/public/{slug}/profile` | public | Name, public profile, opening hours, `ai_policy` (drives the widget's privacy notice) |

## Events
Published: `org.settings_changed`, `org.opening_hours_changed`.

## Error codes
`org.slug_taken`, `org.invalid_timezone`, `org.default_location_required`, `org.invalid_hours`.

## UI
`/settings/business`, `/settings/locations` (hours editor with a weekly grid), `/settings/policies` (settings form, grouped).

## Tests (must exist)
- `test_invalid_timezone_rejected`; `test_exactly_one_default_location`
- `test_opening_hours_replace_is_atomic_and_rejects_overlaps`
- `test_ai_policy_change_requires_ai_settings_permission`
- `test_public_profile_resolves_by_slug_without_session`

## Out of scope
Multi-currency per business; holiday calendars (v1: `availability_exceptions` in scheduling covers closures).
