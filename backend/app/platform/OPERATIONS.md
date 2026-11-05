# Platform operations (batch E, 2026-11-03)

Apply migration `20261104_0700_retention` before starting the new API and worker.
It follows `20261104_0600_ai_budgets`, adds retention indexes, a compact
`job_dedupe` receipt table and an expiry-only idempotency cleanup function.
Use the normal maintenance window: creating indexes takes table locks. After
cleanup first writes dedupe receipts, rolling back to application code that
ignores `job_dedupe` can replay previously accepted actions; retaining the schema
alone does not make that older code safe. Review replay risk before such a
rollback. Migration downgrade refuses to drop a nonempty receipt table.

## Worker capacity and shutdown

`WORKER_CONCURRENCY` defaults to 4 (minimum 2). `WORKER_AI_CONCURRENCY` defaults
to 2 and must be smaller than total concurrency. AI lanes claim only `ai.*`
jobs; the remaining lanes claim only operational jobs, including approvals and
mail. Every lane alternates one event delivery with one job, even when both
queues remain busy. These limits are per worker process, not installation-wide.

`WORKER_SHUTDOWN_GRACE_SECONDS` defaults to 100. SIGTERM/SIGINT stop new polling
and wait for running handlers against one shared deadline. Expiry logs
`worker_drain_timeout`; unfinished jobs are left for lease expiry/retry, never
marked successful by shutdown. Handlers retain their existing idempotency and
five-minute lease requirements. Keep Docker's stop grace greater than the
application grace (at least 120 seconds for the default). If increasing one,
also increase the other. Health heartbeat now comes from successful jobs,
successful event deliveries and idle executor polling, rather than housekeeping.
A fresh heartbeat proves some executor progress, not that every handler or queue
is healthy. Continue monitoring oldest due job/event age and dead work.

## PostgreSQL budget

| Environment setting | Default per process |
| --- | ---: |
| `API_DB_POOL_SIZE` | 4 |
| `API_DB_MAX_OVERFLOW` | 2 |
| `WORKER_DB_POOL_SIZE` | 6 |
| `WORKER_DB_MAX_OVERFLOW` | 0 |
| `DB_POOL_TIMEOUT_SECONDS` | 10 |
| `DB_STATEMENT_TIMEOUT_MS` | 30000 |
| `DB_IDLE_TRANSACTION_TIMEOUT_MS` | 60000 |

One API process and one worker allow at most 12 application connections combined.
For a PostgreSQL limit of 100, budget Keycloak separately (for example cap its
pool at 20), and reserve connections for migration, backup and administration.
These settings do not change Keycloak. Multiply budgets when adding processes
or replicas; worker housekeeping uses the same worker pool.

Statement and idle transaction timeouts are supplied on each new application
connection, including reconnects. Alembic's owner engine and database backup
clients do not use this engine and remain exempt. Restart application processes
after changing settings. In Compose, custom environment variables must also be
forwarded into the corresponding service; putting them in an interpolation file
alone does not inject them into containers. Defaults need no environment changes.
Monitor pool waits, long transactions and dead tuples before tuning further.

## Retention

Worker cron seeds daily global platform and identity cleanup jobs. Each scan
handles at most 500 rows per table/event batch with indexed cutoffs and
`SKIP LOCKED`. Full batches schedule a low-priority continuation a minute later,
so a daily seed can catch up without holding a long transaction or monopolizing
a worker thread. Busy rows wait for a later scan. No external cron is needed.

* Completed, failed and dead jobs: delete after 30 days since last update.
  Queued and running jobs stay. Deduplicated jobs leave a compact permanent
  key/original-id receipt; re-enqueue returns that original id without replay.
* Events: delete after 30 days only when every subscriber completed delivery
  more than 30 days ago. Events with pending, running, dead or undated deliveries
  stay for investigation. Old events with no subscribers are eligible.
* Staff sessions and OIDC login states: delete after absolute/idle expiry or session revocation. Membership,
  role/revocation history and audit remain intact.
* Rate-limit windows: delete after 48 hours (all current application windows are
  at most one hour). Revisit this cutoff if introducing longer windows.
* Cached idempotency responses: delete after their existing 24-hour TTL, including
  suspended businesses. A restricted SECURITY DEFINER function has a fixed
  cutoff, a safe search path and a 500-row limit; public execution is revoked.

Audit events, dedupe receipts and business records are retained. Their growth
still needs disk monitoring; retention is not a disk quota. Existing module
retention (such as assistant-message expiry) remains separate. This does not
implement post-restore erasure reconciliation or a customer-specific retention
policy for financial/business records.

## Streaming data exports

The owner-only export endpoint now streams a ZIP directly from bounded database
batches and compression buffers. It does not stage a complete export in memory
or in `/tmp` (the production API mounts `/tmp` as tmpfs). Memory depends on a
batch of rows, the largest individual row/value and fixed ZIP metadata, rather
than total business data size. Each module keeps its existing fields and
credential/capability redactions. JSON member bytes, manifest format/counts,
filename, media type and no-store policy remain compatible. The ZIP uses data
descriptors/ZIP64 for streaming; the ZIP container's binary bytes can differ.

Export access and the five-per-hour business limit remain unchanged. Completed
archive generation records the same `admin_ops.export.created` event/table count.
The final ZIP directory is withheld until that transaction commits. Failures or
disconnects during generation roll back and release the database connection.
As before, a committed audit event establishes generation, not client receipt.
An incomplete ZIP is not a successful export. Retry interrupted transfers. Slow/stalled clients
can hit the normal database transaction timeouts; completed downloads should be
checked as readable ZIPs. No new migration, configuration, disk volume or manual
step is required for streaming. Rebuild/redeploy the API image as usual.

Module export hooks support `streaming=True` for single-pass rows within the
caller's transaction. Existing direct calls retain eager lists by default.
