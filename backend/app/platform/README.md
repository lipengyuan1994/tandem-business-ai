# platform (M00) — module card

Spec: [docs/roadmap/modules/M00-platform.md](../../../docs/roadmap/modules/M00-platform.md). Never imports `app.modules`.

| File | Use it for |
|---|---|
| `db.py` | `transaction(ctx)` in endpoints/job handlers; `system_transaction()` for queue tables only |
| `context.py` | Immutable `Context`, `system_context()`, `assistant_context()`, per-job `workflow_scope(ctx)` |
| `permissions.py` | `require("x.y.z")`, `require_session()`, `public_context` route dependencies |
| `registry.py` | `Registry` (what modules register), `ExecutorSpec`, `PreviewLine`, `ExecutionMeta` |
| `audit.py` | `record(...)` once per logical mutation; `diff(before, after, fields, redact)` |
| `jobs.py` | `enqueue(db, kind, payload, ...)`; worker primitives |
| `events.py` | `DomainEventPayload` base; `publish(db, ctx, event)` |
| P10-B1 | Nullable correlation/causation/agent/depth on jobs/events; transaction-local context propagation and composition hooks for AI controls; no reverse module imports |
| `errors.py` | `NotFound`, `Conflict`, `Forbidden`, `Invalid`, `RateLimited` (adds `Retry-After`) with `<module>.<reason>` codes; plain `errors[].field` paths under `/api/v1/public/` |
| `http.py` | `client(local=False)` — the only way to make outbound HTTP calls |
| `idempotency.py`, `ratelimit.py` | Create-endpoint idempotency; fixed-window limits |
| `ids.py`, `clock.py`, `money.py`, `canonical.py`, `pagination.py` | UUIDv7 / fixture IDs, injectable UTC clock, Money (`minor_text` / `money_text` write amounts for people: "118.00 USD"), canonical JSON hash, cursors |
| `testing/` | pytest plugin: real Postgres on :5433, `business`, `other_business`, `registry_builder`, `freeze_time`; `testing.auth.ctx_for`, `client_for` |

Tables: `jobs`, `domain_events`, `event_deliveries`, `audit_events` (append-only), `idempotency_keys`, `rate_limits`.

Idempotency lookup holds a business/key advisory lock through creation and response storage, including the first use of a key. Rate-limit hits commit in a separate platform transaction, so rejected requests still count.

Event delivery locks and validates the running claim before calling a subscriber; completion and failure updates require its attempt number. The reaper skips locked deliveries.

`reg.add_job(kind, handler)` keeps `handler(db, ctx, payload)` and success recording in one business transaction.
`reg.add_job(kind, handler, transactional=False)` calls `handler(ctx, payload)` without an open worker transaction;
the handler owns any short database transactions, and the worker records success separately. Both forms use the same
retry, backoff and dead-letter path. Handlers must remain idempotent across crashes and retries.
Non-transactional handlers must finish within the five-minute lease or be idempotent: the reaper can allow another
attempt to run while the old handler continues. Success and failure updates match the claimed job id, attempt count,
`locked_by`, and `running` status. A worker that lost its claim cannot complete a replacement attempt or resurrect a
dead job; `run_job` returns false when its completion is ignored. No automatic lease renewal is provided.

`reg.add_cron(name, job_kind, every_seconds)` enqueues one global job per slot. With `per_business=True`, it enqueues
one job per active business with that business id and dedupe key `cron:{name}:{slot}:{business_id}`. Discovery uses
`public.active_business_ids()`, a SECURITY DEFINER function with a safe search path and execution granted only to
the owner and `smb_app`.

`LOCAL_INFERENCE_HOSTS` is an optional comma-separated list of exact DNS hostnames (default empty). Only
`client(local=True)` permits HTTP and private/loopback DNS for those names. Other HTTPS traffic still requires
`EGRESS_ALLOWLIST`; literal IPs, link-local/metadata, multicast, reserved and unspecified addresses stay blocked.
Redirects remain disabled, with the same timeout and response-size checks. For a local server, set
`LOCAL_INFERENCE_HOSTS=localhost` and use its hostname and port, such as `http://localhost:8080`.
For configured local hosts, the transport resolves DNS once, validates every answer, and connects using a validated
numeric address, retaining the original Host header and TLS hostname. This prevents DNS rebinding between the guard
and connection; the entire `169.254.0.0/16` range remains blocked, including when `resolve=False`. Each local hostname
has its own connection pool. The opt-in client bypasses environment proxies so they cannot replace the pinned
destination. Default clients (and a local client with no configured hosts) retain their existing transport behavior.

Database engines hide bound parameters. Logging suppresses database exception details (including driver/chained messages), retains frame locations and exception types, and redacts rendered exceptions before output.

Production settings, connection budgets, worker lanes/drain, and daily retention: [OPERATIONS.md](OPERATIONS.md).
