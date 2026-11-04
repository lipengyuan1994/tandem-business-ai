# Module specifications

One file per module. A module spec is the **contract** an agent implements: tables, rules, public functions, endpoints, permissions, events, executors, error codes and required tests. Copy [_TEMPLATE.md](_TEMPLATE.md) for any new module (which needs an ADR).

| ID | Module | Release | Built in | Tier | Kind | Depends on (besides platform) |
|---|---|---|---|---|---|---|
| M00 | [platform](M00-platform.md) | MVP | P1 | A | PRE | — |
| M01 | [identity](M01-identity.md) | MVP | P2 | A | PRE | org |
| M02 | [org](M02-org.md) | MVP | P2 | B | PRE | — |
| M03 | [crm](M03-crm.md) | MVP | P3 | B | PRE | approvals |
| M04 | [conversations](M04-conversations.md) | MVP | P3 | B | PRE | org, crm, approvals, notifications |
| M05 | [catalog](M05-catalog.md) | MVP | P3 | B | PRE | org, approvals |
| M06 | [scheduling](M06-scheduling.md) | MVP | P3 | A (availability, booking) / B (CRUD) | PRE | org, crm, catalog, inventory, conversations, approvals, notifications |
| M07 | [inventory](M07-inventory.md) | MVP | P3 | A (ledger, reservations) / B (CRUD) | PRE | org, catalog, approvals |
| M08 | [approvals](M08-approvals.md) | MVP | P2 | A | PRE | org, identity |
| M09 | [audit](M09-audit.md) | MVP | P2 | B | PRE | identity |
| M10 | [notifications](M10-notifications.md) | MVP / v1 | P2 / P8 | A (outbox) / B (UI) | PRE | identity, approvals |
| M11 | [dashboard](M11-dashboard.md) | MVP | P3 / P5 | B | PRE (+ EVENT briefing) | org, crm, scheduling, inventory, conversations, approvals, notifications |
| M12 | [marketing](M12-marketing.md) | MVP (draft) | P6 | B | PRE (segments) + EVENT (AI draft) | org, crm, catalog, scheduling, approvals, notifications |
| M13 | [ai](M13-ai.md) | MVP | P5 | A | EVENT | org, identity, crm, catalog, scheduling, inventory, conversations, marketing, approvals, sales, purchasing, dashboard, notifications |
| M14 | [voice](M14-voice.md) | Stretch / v1 | P6 / P8 | A | EVENT | ai, conversations |
| M15 | [files](M15-files.md) | v1 | P8 | B | — | — |
| M16 | [sales](M16-sales.md) | v1 | P8 | B | — | org, crm, catalog, inventory, scheduling, approvals, notifications |
| M17 | [purchasing](M17-purchasing.md) | v1 | P8 | B | PRE (procurement groundwork) | org, catalog, inventory, approvals, notifications |
| M18 | [admin_ops](M18-admin-ops.md) | v1 (System page in P6) | P6 / P8 | A | PRE (System page, export, erasure) | org, identity, files, notifications, crm |

Shared rules that apply to every module, so specs do not repeat them:
- Every table follows [05-data-model.md](../05-data-model.md#conventions-for-every-table): `id`, `business_id`, `version`, `created_at`, `updated_at`, composite FKs and row-level security. Specs list only the *other* columns.
- Every list endpoint is paginated, every update requires `version`, and every mutation writes audit ([04](../04-repository-and-conventions.md#api-conventions), [07](../07-workflow-engine.md#audit)).
- Every module's tests include, besides those listed: a denied role per endpoint, cross-business 404 and a version conflict on update.
