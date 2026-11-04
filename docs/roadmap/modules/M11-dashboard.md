# M11 · dashboard

| Field | Value |
|---|---|
| Release / phase | MVP · P3 (Today) / P5 (AI briefing via ai module) |
| Tier / kind | B · PRE (the AI briefing is EVENT and lives in M13) |
| Depends on | platform, org, crm, scheduling, inventory, conversations, approvals, notifications |
| Owns tables | none |

## Purpose
The "Today" page and navigation counters. It is read-only aggregation through other modules' public functions. The AI daily briefing is produced by `ai` (U6) and shown here if available. Dashboard does not call ai; the frontend calls ai's endpoint.

## API
| Method | Path | Permission | Returns |
|---|---|---|---|
| GET | `/dashboard/today` | session + any module read | `agenda` (today's appointments, local time), `open_conversations` by status, `pending_approvals` (count + top 5 the user can decide), `low_stock` (from `list_below_reorder`), `delivery_issues` (count of `unknown` and `failed`) — each section omitted if the user lacks that module's read permission |
| GET | `/dashboard/counters` | session | `{inbox_waiting_on_staff, approvals_i_can_decide, unread_notifications}` (polled every 30 s) |

## Business rules
1. Every section checks its own permission. A missing permission leaves the section out rather than returning 403 for the whole page.
2. "Today" uses the business time zone's local day bounds (`platform.clock.local_day_bounds`).
3. Response time target: < 150 ms with the seed dataset (each section is one or two indexed queries).

## UI
`/today`: cards for Agenda (timeline by resource), Inbox, Approvals (approve inline for simple items), Low stock (with "Create reorder draft"), Delivery issues, and from P5 the AI briefing card ("5 things to know today", each line linked to its source).

## Tests (must exist)
- `test_today_sections_respect_permissions`
- `test_today_uses_business_local_day` (business in America/Denver at 23:30 local)
- `test_counters_approvals_only_counts_types_user_can_approve`

## As built (P3, 2026-11-03)
- PR-01: Today computation now lives in `dashboard.service.today_summary(db, ctx) -> TodaySummary`, exported through
  `dashboard.public`. The API delegates to it with unchanged fields, full-day agenda and permission-filtered sections.
  A seeded regression compares the public DTO and endpoint for owner and inventory-manager permissions.
- `test_today_is_fast_on_seed_data` measures `/dashboard/today` on the seed (median of 5 calls; 21 ms locally, target < 150 ms).
- Nav badges use `/dashboard/counters`, polled every 30 s. The Today page shows cards only for sections the API returned. Low stock reuses the inventory suggestions panel ("Create draft"). Inline approval is not built; cards link to the proposal.
