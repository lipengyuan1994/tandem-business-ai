# M00 · platform (kernel)

| Field | Value |
|---|---|
| Release / phase | MVP · P1 |
| Tier / kind | **A** · PRE |
| Depends on | nothing in `app.modules` (**must never import a module**) |
| Owns tables | `jobs`, `domain_events`, `event_deliveries`, `audit_events`, `idempotency_keys`, `rate_limits` (DDL in [05](../05-data-model.md#platform-owned-tables-m00)) |

## Purpose
Shared technical foundation used by every module: configuration, database sessions with tenancy, request context, permissions, registry, audit writer, jobs, events, errors, outbound HTTP, IDs, time, money, pagination, logging and test helpers. It contains **no business concepts** beyond "business" and "user" as identifiers.

## Components and their contracts

| File | Provides |
|---|---|
| `config.py` | `Settings` (pydantic-settings) loaded from env; `get_settings()` cached. Fails fast at startup on missing required values |
| `db.py` | `engine`, `SessionLocal`, `transaction(ctx)` context manager ([04](../04-repository-and-conventions.md#context-and-transactions-platformcontextpy-platformdbpy)); `system_transaction()` with no business (for platform queue tables only) |
| `models.py` | `Base` with naming convention; `BusinessScopedBase` mixin (`id`, `business_id`, `version`, `created_at`, `updated_at`, `unique(business_id,id)`); platform table models |
| `context.py` | `Context` dataclass; `system_context(business_id)`; `assistant_context(business_id, on_behalf_of_user_id | None, permissions, ai_run_id)` |
| `permissions.py` | `PermissionRegistry`; `require(permission)` FastAPI dependency (session → user → active business → permissions → CSRF on unsafe methods → `Context`); `public_context(slug)` dependency for public routes (slug → business through a resolver **registered by org**). Session lookup is delegated to a resolver **registered by identity at startup**, so platform does not import identity |
| `registry.py` | `Registry` collecting routers, permissions, executors (`ExecutorSpec`), job handlers, event subscribers, scheduled jobs, resolvers (session, public business), and data-lifecycle handlers (`export`, `erase_customer`, `apply_retention`) used by admin_ops; frozen after startup |
| `audit.py` | `record(db, ctx, *, action, target_type, target_id, summary, changes=None, proposal_id=None)`; `diff(before, after, redact)` |
| `jobs.py` | `enqueue(...)`; `Worker` (claim, run, retry, reaper, cron); see [07](../07-workflow-engine.md#jobs) |
| `events.py` | `DomainEvent` base model; `publish(db, ctx, event)`; delivery loop (shared with the worker) |
| `canonical.py` | `canonical_json(obj) -> bytes`; `sha256_hex(obj) -> str` |
| `errors.py` | `DomainError` base (`code`, `status`, `detail`, extra fields); `NotFound` 404, `Conflict` 409, `Forbidden` 403, `Invalid` 422, `RateLimited` 429; exception handlers producing RFC 9457 |
| `http.py` | `client()` returning an `httpx.Client` with egress allowlist, IP-range blocking, timeouts and size cap ([06](../06-security-and-privacy.md#egress)) |
| `idempotency.py` | `idempotent(request, key)` helper for create endpoints |
| `ratelimit.py` | `hit(key, limit, window_s)` using `rate_limits` |
| `ids.py` | `new_id() -> UUID` (UUIDv7); `fixture_id(key) -> UUID` (uuid5 namespace, for seeds and tests) |
| `clock.py` | `now() -> datetime` (UTC, injectable for tests); `to_zone(dt, tz)`; `local_day_bounds(date, tz)` |
| `money.py` | `Money(amount_minor: int, currency: str)` with safe add/compare |
| `pagination.py` | `Page[T]`, cursor encode/decode (opaque base64 of `(created_at, id)`) |
| `logging.py` | structlog setup; request-id middleware; redaction processor |
| `health.py` | `/healthz`, `/readyz` routes (public allowlist) |
| `testing/` | pytest plugin: database setup/truncate, `ctx_for`, `login_as`, `freeze_time`, factories for business and user |

## Business rules
1. `transaction(ctx)` always sets `app.business_id` and `app.user_id` with transaction-local scope.
2. `require()` rejects: no or expired or revoked session (401); missing CSRF on POST/PATCH/PUT/DELETE (403 `auth.csrf_failed`); no active membership in the session's active business (403); missing permission (403).
3. Audit records are written in the caller's transaction. The function never commits.
4. `jobs.enqueue` with an existing `dedupe_key` is a no-op that returns the existing job ID.
5. `events.publish` creates one delivery row per subscriber registered for that event type at publish time.
6. `http.client()` refuses non-allowlisted hosts and private, loopback, link-local and metadata IPs after DNS resolution.
7. `platform` never imports from `app.modules` (architecture test).
8. `Registry.add_job(..., transactional=False)` runs `handler(ctx, payload)` without a worker-held transaction;
   success is recorded separately, with the same failure/retry path as transactional handlers.
   Success/failure updates require the claimed id, attempt count, `locked_by`, and `running` status. Handlers must
   finish within the five-minute lease or be idempotent; a replacement may overlap an old handler after lease expiry.
9. `Registry.add_cron(..., per_business=True)` enqueues a deduplicated job for each active business per slot, using
   the hardened `active_business_ids()` SECURITY DEFINER function. Other crons stay global.
10. `http.client(local=True)` permits HTTP and private/loopback DNS only for exact names in the comma-separated
    `LOCAL_INFERENCE_HOSTS` setting (default empty). Normal clients and all other destination checks stay unchanged.
    Configured local hosts use direct connections pinned to validated DNS answers, preserving Host/TLS hostname,
    with separate pools per hostname. The opt-in client bypasses environment proxies; normal clients retain them.
    Link-local addresses (including all of `169.254.0.0/16`) remain forbidden even with `resolve=False`.

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/healthz` | public | Process up |
| GET | `/readyz` | public | DB, migrations at head, Keycloak reachable |

## Error codes
`auth.unauthenticated`, `auth.csrf_failed`, `auth.permission_denied`, `auth.no_active_business`, `platform.rate_limited`, `platform.idempotency_key_reused` (same key, different body), `platform.validation_error`.

As built (2026-11-03, security re-scan F5-F7):
- `platform.rate_limited` (429) always carries the standard `Retry-After` header in whole seconds (at least 1), equal to
  `retry_after_seconds` in the body and to what is left of the limiter's fixed window.
- `platform.validation_error` (422) lists `errors[]` as `{field, code}`. On staff routes `field` is Pydantic's full dotted path.
  Under `/api/v1/public/` it holds only JSON field names and list positions (`lines.0.unit_price_minor`; `""` for a rule about
  the whole body): the names Pydantic gives to the members of a union (class and function names) are dropped, and repeated
  entries are merged.
- The API description (`/api/v1/openapi.json`, `/docs`) is served only when `APP_ENV` is not `prod`; `python -m app.cli openapi`
  (`make gen-api`) and the contract test do not use the URL.

## Tests (must exist)
- `test_transaction_sets_rls_variables_and_resets_after_commit`
- `test_rls_hides_other_business_rows` (a two-business fixture with a dummy table created in a test migration)
- `test_require_rejects_missing_csrf_on_unsafe_methods`
- `test_audit_rolls_back_with_business_change`
- `test_job_claim_is_exclusive_under_concurrency` (two threads, one job)
- `test_job_retry_backoff_and_dead_letter`
- `test_reaper_requeues_stale_running_jobs`
- `test_cron_enqueue_is_deduplicated`
- `test_event_delivered_once_per_subscriber_and_retried_on_failure`
- `test_http_client_blocks_private_and_metadata_ips`
- `test_canonical_json_stable_for_key_order_and_decimals`
- `test_idempotency_returns_original_response_and_rejects_changed_body`
- Architecture tests listed in [04](../04-repository-and-conventions.md#module-boundaries-are-tested)

## Out of scope
Business entities; UI; anything AI.
