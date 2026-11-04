# M09 · audit

| Field | Value |
|---|---|
| Release / phase | MVP · P2 |
| Tier / kind | B · PRE |
| Depends on | platform, identity |
| Owns tables | none (reads platform's `audit_events`) |

## Purpose
Browse, filter and export the audit log. Writing audit events is a platform function ([07](../07-workflow-engine.md#audit)), so every module can do it without depending on this module.

## Business rules
1. Read-only. No endpoint changes audit events.
2. Results are business-scoped (row-level security) and ordered by `occurred_at desc, id desc`.
3. Export produces CSV with redacted `changes`, streamed. Exports are themselves audited (`audit.exported`).
4. Actor display names come from `identity.get_user_brief`. Assistant actors show "Assistant (on behalf of <user>)" or "Assistant (intake)".

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/audit/events` | `audit.event.read` | `?from&to&actor_id&actor_kind&action_prefix&target_type&target_id&proposal_id` |
| GET | `/audit/events/{id}` | read | |
| GET | `/audit/export.csv` | `audit.export` | Same filters; max 100,000 rows |

## Permissions
`audit.event.read`, `audit.export`.

## UI
`/audit`: filter bar, table (time in local zone, actor, action, target link, summary), detail drawer with the redacted diff, export button. Record pages (customer, appointment, proposal) embed a small "History" panel through the same endpoint with `target_id`.

## Tests (must exist)
- `test_audit_filters_and_pagination`
- `test_export_is_audited_and_redacted`
- `test_auditor_role_can_read_and_export_but_operator_cannot_export`

## Out of scope (v1 in P8)
Nightly hash chain and export to Object Storage (`audit.chain_and_export` job).
