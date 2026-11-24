# M20 · accounting (v1)

| Field | Value |
|---|---|
| Release / phase | v1 · gap-map money side (2026-11-24) |
| Tier / kind | A (OAuth tokens, outbound writes to the business's books) |
| Depends on | platform, org, sales, purchasing |
| Owns tables | `accounting_connections`, `accounting_sync_records` |

## Purpose
Copy the business's money records into its accounting system so the books stay current without retyping. First
provider: QuickBooks Online ([ADR-021](../03-decisions.md#adr-021-accounting-sync-quickbooks-online-first-push-only)).
Tandem stays the system of record for operations; the accounting system is the system of record for the books. The
module never changes sales or purchasing data and never reads QuickBooks data back into them.

## Tables
`accounting_connections` — one per business: provider, Intuit company (`realm_id`), status
(`not_connected|pending|connected|needs_reconnect|disconnected`), sealed access and refresh tokens with expiries, a
one-time OAuth state hash, company name, country and home currency, account mapping (7 QuickBooks account ids),
`sync_from`, `auto_sync`, sync lease and last-run fields.

`accounting_sync_records` — one row per Tandem record per QuickBooks company: entity type, local id, label, local
version, remote id, SyncToken, remote state (`active|voided|deleted`), content hash, status (`synced|failed|skipped`),
attempts, last error, next attempt, synced at. Unique on (business, realm, entity type, local id).

Both tables use forced business RLS. Tokens are never exported, logged or audited.

## Business rules
1. Only an owner connects, maps accounts or disconnects. Connecting is refused under `DATA_PROFILE=regulated`.
2. The OAuth `state` is single use and expires after 10 minutes; the callback checks it before exchanging the code.
3. Refresh tokens rotate: every refresh stores the new one. A refused refresh sets `needs_reconnect` and clears the
   tokens; nothing is lost, the next run after reconnecting catches up.
4. Only records dated on or after `sync_from` are sent. Invoices are sent once issued; drafts and quotes never.
5. A record is re-sent only when its Tandem version changed and the QuickBooks body changed.
6. A voided Tandem payment is voided in QuickBooks; a voided bill is deleted there (QuickBooks bills cannot be voided);
   a record voided before it ever reached QuickBooks is skipped.
7. A credit note becomes a credit memo applied to its invoice through a zero-amount payment.
8. Records in a currency other than the QuickBooks home currency are skipped with a reason.
9. A record that QuickBooks rejects backs off 5 min, 15 min, 1 h, 6 h, 24 h; staff can retry it at once. Records that
   depend on it wait for it.
10. Lost sign-in, throttling, outages and egress denial stop the run without marking records failed.

## Public interface (`public.py`)
None. The module only reads `sales.public` and `purchasing.public` ledger feeds.

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/v1/accounting/quickbooks` | `accounting.sync.read` | Status, company, mapping, counts |
| POST | `/api/v1/accounting/quickbooks/connect` | `accounting.connection.manage` | Returns Intuit's authorize URL |
| GET | `/api/v1/accounting/quickbooks/callback` | `accounting.connection.manage` | Intuit redirect; 303 to `/settings/accounting` |
| PUT | `/api/v1/accounting/quickbooks/settings` | `accounting.connection.manage` | Mapping, `sync_from`, `auto_sync`; needs `version` |
| GET | `/api/v1/accounting/quickbooks/accounts` | `accounting.connection.manage` | Active QuickBooks accounts for the mapping |
| POST | `/api/v1/accounting/quickbooks/disconnect` | `accounting.connection.manage` | Revokes and forgets the tokens; keeps the log |
| POST | `/api/v1/accounting/quickbooks/sync` | `accounting.sync.run` | Queues a run unless one is waiting |
| GET | `/api/v1/accounting/quickbooks/records` | `accounting.sync.read` | Paged log, filter by status and type |
| POST | `/api/v1/accounting/quickbooks/records/{id}/retry` | `accounting.sync.run` | Clears the back-off and queues a run |

## Permissions
`accounting.sync.read` — see status and log (owner, admin, demo reviewer, auditor).
`accounting.sync.run` — sync now and retry (owner, admin, demo reviewer).
`accounting.connection.manage` — connect, map, disconnect (owner).

## Executors and jobs
Job `accounting.sync` (non-transactional, talks to QuickBooks between short transactions). Cron `accounting-sync`
every 15 minutes per business (runs only when `auto_sync`).

## Error codes
`accounting.not_configured`, `accounting.regulated_profile`, `accounting.not_connected`,
`accounting.version_conflict`, `accounting.oauth_state_invalid`, `accounting.oauth_declined`,
`accounting.oauth_response_invalid`, `accounting.quickbooks_<kind>` (auth, throttled, unavailable, config,
validation, …), `accounting.record_not_found`, `accounting.record_already_synced`.

## UI
Settings → Accounting (`/settings/accounting`): connect or reconnect, company and currency, counts, sync now,
account mapping, sync start date, automatic sync switch, disconnect, and the sync log with retry.

## Tests (must exist)
- Connect flow (state single use, declined, bad code), not configured, regulated profile, owner-only connect.
- Full sync of invoice, payment, bill and bill payment; rerun sends nothing.
- Payment void; credit note applied; void bill deleted; voided-before-sync skipped.
- Back-off and retry; stale SyncToken update; currency skip; token refresh and needs-reconnect; throttling.
- Disconnect revokes; export omits tokens; erasure redacts labels; business isolation.

## Out of scope
Xero; reading QuickBooks back into Tandem; per-item income accounts and invoice line items mapped to QuickBooks
products; bill line items; supplier credits; multi-currency companies; tax codes for non-US QuickBooks companies.
