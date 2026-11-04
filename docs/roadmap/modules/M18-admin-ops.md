# M18 · admin_ops (v1)

| Field | Value |
|---|---|
| Release / phase | v1 · P8 (a minimal System page may ship in P6) |
| Tier / kind | A |
| Depends on | platform, org, identity, files |
| Owns tables | `erasure_requests`, `export_jobs`, `system_checks` |

## Purpose
Operating the installation from inside the app: health and version, backups status, data export and erasure, retention enforcement.

## Features
- **System page** (`/system`, owner and admin): version and upgrade log, uptime, job queue depth and age, dead jobs (retry or discard, audited), outbox issues, last backup time and result (from the backup container's status file mounted read-only), certificate expiry, disk usage, AI usage vs budget.
- **Data export:** owner requests a full export → job builds a ZIP of JSON per module (each module registers an export handler with `reg.add_export_handler(...)`; admin_ops calls them through the platform registry, so it needs no dependency on business modules) → stored in Object Storage → pre-signed link valid 24 h, emailed or shown once. Audited.
- **Erasure:** owner files a request for a customer → confirmation step → job calls every registered erasure handler (`reg.add_erasure_handler`) in reverse dependency order (highest layer first) → report. Audited. Explains backup retention.
- **Retention enforcement:** nightly job applies the retention settings ([06](../06-security-and-privacy.md#retention-and-erasure)) through registered retention handlers (`reg.add_retention_handler`).

## Tests
- Export contains every module's data for one business and none from another.
- Erasure removes PII across modules and keeps IDs and amounts; it is idempotent.
- Retention deletes only rows older than the configured period.

## As built: System page only (P6-09, 2026-11-03)
`GET /api/v1/admin/system` (permission `admin_ops.system.read`, owner and admin) and the `/system` page show: application version (`APP_VERSION`, set from `VERSION` in the production compose file), queue age of the oldest waiting task, failed ("dead") tasks and internal notifications, messages that need attention (failed or unknown), the last backup (read from the status file the backup container writes; mounted read-only into the api container), and AI usage (null: the AI layer is not installed). All counts are for the signed-in business only. `admin_ops` now depends on `notifications` (outbox counts).

## As built: data export and customer erasure (2026-11-03)
- **Export:** `POST /api/v1/admin/exports` (permission `admin_ops.data.manage`, owner only) returns a ZIP at once (no job or Object Storage link; the data of one small business is small) with `<module>/<table>.json` files and a `manifest.json` with row counts. Each module registers a handler (`add_export_handler`, dumping its own tables with `platform/export.dump_models`). Password hashes, sessions, visitor tokens and secrets are left out. Audited (`admin_ops.export.created`).
- **Erasure:** `POST /api/v1/admin/erasures {customer_id, confirm_name}`; the typed name must equal the customer's display name. Checks (`add_erasure_check`) first: open approval proposals that mention the customer, and held or confirmed future appointments, return 409 `admin_ops.erasure_blocked` with the reasons. Otherwise every module's `add_erasure_handler` runs in reverse dependency order in one transaction: crm keeps the customer id as a tombstone ("Erased customer", contact points set to "[erased]" and inactive, notes, tags and timeline deleted); conversations and notification outbox rows lose their text, recipients and variables; scheduling clears appointment notes; audit, approval history and consent evidence are redacted by SECURITY DEFINER functions (`erase_redact_*`, which refuse other businesses) because those tables are append-only. Amounts, appointments and stock stay. A suppression for the customer's e-mail address is kept so they are never mailed again. Repeating the request returns the first result. Recorded in `erasure_requests` and the audit log; `GET /admin/erasures` lists them.
- **UI:** System page "Your data" panel (download, list of erasures); customer page "Erase personal data…" dialog with the typed-name confirmation and the blocking reasons.
- **Not built:** export as an asynchronous job with a pre-signed link, retention enforcement (`export_jobs`, `system_checks` tables are unused), erasure from backups (documented: backups keep the data until they expire).

### Review fixes (2026-11-03, Codex GPT-6.1 Sol review, all 18 findings fixed)
- **Linked records:** each module reports the ids of the person's records (`add_erasure_links`: appointments, conversations, messages, the outbox rows of their replies, proposals that mention any of these). Records carrying one of those ids lose *all* their free text (`scrub_jsonb` keeps only ids, timestamps and numbers), so a rejected rename to a different name or a cancellation reason in an audit summary is removed too. Other records are redacted by the person's names, addresses and numbers. `redact_jsonb` now walks the JSON value and never edits numbers, keys or ids.
- **Wider coverage:** inactive contact points (archived customers), given/family/organisation names, phone-only chat visitors (numbers matched with or without a country code), web-chat replies in the outbox, in-app notifications, appointment cancellation reasons, stock movement notes and reorder drafts, campaign purposes, revisions and segments, personal tags, domain event and job payloads, and cached idempotent responses. Open proposals that refer to the person's appointments or conversations block an erasure.
- **Promise kept:** marketing records an `erased` suppression for each e-mail address before crm blanks it.
- **Security:** every SECURITY DEFINER function searches `pg_temp` last (`pg_catalog, public, pg_temp`), and new installs no longer grant TEMPORARY to the application role. New redaction functions are executable by `smb_app` only.
- **Concurrency:** the customer row is locked before anything is read; a unique index allows one completed erasure per customer.
- **Export:** includes the business's jobs, domain events, event deliveries and erasure requests; signed unsubscribe links are left out; no row cap (rows are streamed in batches).
- Migration `20261027_1500_erasure_hardening`. Residual: audience snapshots keep their segment definition, because their hash proves what was approved; backups keep everything until they expire.

## As built: regulated retention hold (P8-23, 2026-11-03)

With `DATA_PROFILE=regulated`, the same owner-only typed-name action restricts the
customer only after the same open-proposal and upcoming-appointment checks as erasure,
preserving personal notes, timeline, conversation text,
appointment notes, consent evidence and audit history. Separate registry restriction
handlers deactivate/blank contact points, suppress each e-mail and block queued sends;
standard erasure hooks are unchanged. Erasure requests gain `restricted`, with idempotence
across profile switches. No restriction-release workflow is available, even to owners.
System shows the profile, host/backup attestations and the compliance limitation.
See [regulated-profile.md](../../runbooks/regulated-profile.md) for coverage and limits.

Existing suppressions are promoted to `erased` by a narrow tenant-guarded SECURITY
DEFINER function; normal application access to the suppression table stays append-only.

### Review hardening (P8-23)

Owner decision: restriction returns the same 409 and reasons as erasure while open
proposals reference the customer/their records or held/confirmed appointments are upcoming.
Booking and holds use a CRM public eligibility helper holding `FOR SHARE` until commit.
Outbox and conversation restriction timestamps persist independently of delivery outcomes
and customer links. Cached idempotency responses mentioning the customer ID are deleted,
while record text and history stay. Session rotation preserves the original authentication
time; the regulated absolute deadline is measured from that time, never the latest rotation.
Migration: `20261028_1200_regulated_hardening` (after the original profile migration).
