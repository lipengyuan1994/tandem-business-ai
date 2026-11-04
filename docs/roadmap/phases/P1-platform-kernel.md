# P1 · Platform kernel (Nov 3) — PRE

**Goal:** a running development stack and a tested kernel, so every later module is "fill in the template".
**Entry:** P0 exit gate passed (new AGENTS.md, toolchain approved).

### P1-01 · Repository skeleton and toolchain
Kind: PRE · Tier: B · Module: repo · Size: M · Depends on: P0-03, P0-04
Read first: [04](../04-repository-and-conventions.md), [03 ADR-002, ADR-005, ADR-016](../03-decisions.md)
Create/modify: `Makefile`, `LICENSE` (MIT), `.env.example`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/app/__init__.py`, `frontend/*` (Vite React TS scaffold), `.editorconfig`; remove the empty root `ui/`, `scripts/`, `tests/` (owner confirmed in P0-01).
Steps:
1. `backend/pyproject.toml`: Python `>=3.13,<3.14`. Runtime deps: `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2.0`, `psycopg[binary]`, `alembic`, `pydantic>=2`, `pydantic-settings`, `structlog`, `httpx`, `authlib`, `python-multipart`, `jinja2`, `prometheus-client`. Dev deps: `pytest`, `pytest-cov`, `mypy`, `ruff`, `respx`, `pip-licenses`. Configure ruff (line length 120; rules `E,F,I,B,UP,S,ASYNC,RUF`), mypy strict for `app/`, pytest markers `integration`, `concurrency`.
2. `uv lock` to pin versions.
3. Frontend: `pnpm create vite frontend --template react-ts`, then add the packages from ADR-005. Set `strict: true` and `noUncheckedIndexedAccess: true`. Initialise shadcn/ui with Tailwind v4. Configure ESLint with the `no-explicit-any` error.
4. `Makefile` with every target from [04](../04-repository-and-conventions.md#makefile-targets-contract). Targets for later phases print "not yet implemented (task Px-yy)" and exit 0.
5. `.env.example` exactly as in [04](../04-repository-and-conventions.md#environment-variables-envexample).

Done when:
- [ ] `cd backend && uv run python -c "import fastapi, sqlalchemy"` works.
- [ ] `cd frontend && pnpm build` succeeds.
- [ ] `make help` lists all targets.

### P1-02 · Development Compose stack
Kind: PRE · Tier: A · Module: deploy · Size: M · Depends on: P1-01
Read first: [10](../10-vultr-infrastructure.md), [05 roles](../05-data-model.md#tenancy-and-row-level-security), [M01 realm](../modules/M01-identity.md#keycloak-realm-deploykeycloakrealm-smbjson)
Create: `deploy/compose/compose.dev.yml`, `deploy/compose/postgres-init.sql`, `deploy/compose/Caddyfile.dev`, `deploy/keycloak/realm-smb.json` (dev variant with a seeded test user `owner@example.com`, a known TOTP secret and `localhost` redirect URIs), `backend/Dockerfile.dev`.
Steps:
1. Services: `postgres` (`pgvector/pgvector:pg17`), `db-test` (same image, tmpfs data, port 5433 on localhost only), `keycloak` (pinned version, `start-dev --import-realm`, DB `keycloak`), `api` (uvicorn `--reload`, source mounted), `worker` (`python -m app.worker` run by `watchfiles`, which restarts it when a `*.py` file under `app/` changes; source mounted), `web` (vite dev server), `caddy` (TLS `internal`, routes `/api` → api, `/auth` → keycloak, `/` → vite).
2. `postgres-init.sql` creates the roles `smb_owner` (BYPASSRLS), `smb_app` and `keycloak`, the databases `smb` and `keycloak`, and default privileges so that tables created by `smb_owner` grant `SELECT, INSERT, UPDATE, DELETE` to `smb_app`.
3. Only `caddy` (443 → localhost:8443) and `db-test` (5433) publish ports, on 127.0.0.1.

Done when:
- [ ] `make dev` starts everything; `https://localhost:8443/auth/realms/smb` responds; `https://localhost:8443/` shows the Vite page.
- [ ] `psql` as `smb_app` cannot `CREATE TABLE` in `smb`.

### P1-03 · Platform basics: config, logging, errors, ids, clock, money, canonical, pagination
Kind: PRE · Tier: B · Module: M00 · Size: M · Depends on: P1-01
Read first: [M00](../modules/M00-platform.md), [04 API conventions](../04-repository-and-conventions.md#api-conventions)
Create: `backend/app/platform/{config,logging,errors,ids,clock,money,canonical,pagination}.py` and tests.
Steps: implement each contract in M00. UUIDv7 per RFC 9562 (48-bit ms timestamp, version 7, variant bits, random). `fixture_id(key)` = `uuid5(NAMESPACE, key)` with a constant namespace. Error classes map to RFC 9457 bodies.

Done when:
- [ ] Tests: `test_uuid7_sortable_and_versioned`, `test_canonical_json_stable_for_key_order_and_decimals`, `test_problem_details_shape`, `test_local_day_bounds_dst`, `test_money_rejects_currency_mismatch`, `test_cursor_roundtrip`.
- [ ] `make check` passes (only the parts implemented so far).

### P1-04 · Database layer, models base, row-level security helpers, first migrations
Kind: PRE · Tier: A · Module: M00 (+ org `businesses` table) · Size: M · Depends on: P1-02, P1-03
Read first: [05](../05-data-model.md), [M00](../modules/M00-platform.md), [M02 tables](../modules/M02-org.md#tables)
Create: `app/platform/db.py`, `app/platform/models.py`, `backend/alembic.ini`, `backend/migrations/{env.py,helpers.py}`, migrations `…_platform_extensions.py` (`btree_gist`, `pg_trgm`, `vector`), `…_org_businesses.py` (businesses table only, plus the `public_business_by_slug` security-definer function), `…_platform_tables.py`; `app/modules/org/models.py` with `Business` only.
Steps:
1. `transaction(ctx)` and `system_transaction()` as in [04](../04-repository-and-conventions.md#context-and-transactions-platformcontextpy-platformdbpy).
2. `helpers.enable_business_rls(op, table)` and `create_updated_at_trigger(op, table)`.
3. Platform tables per [05](../05-data-model.md#platform-owned-tables-m00). `audit_events`: revoke UPDATE and DELETE from `smb_app`.
4. Alembic runs with `DATABASE_MIGRATION_URL`; the app uses `DATABASE_URL`.

Done when:
- [ ] `make migrate` on an empty DB succeeds; `alembic downgrade base && alembic upgrade head` succeeds.
- [ ] `test_transaction_sets_rls_variables_and_resets_after_commit` and `test_rls_hides_other_business_rows` pass.
- [ ] As `smb_app`, `UPDATE audit_events` fails with permission denied (test).

### P1-05 · Context, permissions, registry, app factory, health
Kind: PRE · Tier: A · Module: M00 · Size: M · Depends on: P1-04
Read first: [M00](../modules/M00-platform.md), [02](../02-architecture.md), [06 permission model](../06-security-and-privacy.md#permission-model)
Create: `app/platform/{context,permissions,registry,health}.py`, `app/main.py` (`create_app()`), `app/modules/dependencies.py` (full graph from [02](../02-architecture.md#backend-module-map-and-dependency-graph), even for modules that do not exist yet).
Steps:
1. `Registry` with `add_router`, `add_permissions`, `add_executor`, `add_job`, `subscribe`, `add_cron`, `set_session_resolver`, `set_public_business_resolver`, `add_export_handler`, `add_erasure_handler`, `add_retention_handler`; `freeze()`.
2. `require(permission)` uses the registered session resolver. In tests a fake resolver is injected.
3. `create_app()` imports each existing module package and calls `register(reg)`, then mounts routers under `/api/v1`, installs problem handlers, request-ID middleware and `/healthz`, `/readyz`.

Done when:
- [ ] `test_require_rejects_missing_csrf_on_unsafe_methods`, `test_require_returns_context_with_permissions`, `test_registry_frozen_after_startup` pass.
- [ ] `GET /api/v1/healthz` returns 200 in the dev stack.

### P1-06 · Audit writer
Kind: PRE · Tier: A · Module: M00 · Size: S · Depends on: P1-05
Read first: [07 Audit](../07-workflow-engine.md#audit)
Done when:
- [ ] `test_audit_rolls_back_with_business_change`, `test_audit_diff_redacts_fields`, `test_audit_fills_actor_from_context` pass.

### P1-07 · Jobs, worker loop, cron, reaper
Kind: PRE · Tier: A · Module: M00 · Size: L · Depends on: P1-05
Read first: [07 Jobs](../07-workflow-engine.md#jobs)
Create: `app/platform/jobs.py`, `app/worker.py` (thread pool of `WORKER_CONCURRENCY`, graceful shutdown on SIGTERM, heartbeat file `/tmp/worker-heartbeat`).
Done when:
- [ ] `test_job_claim_is_exclusive_under_concurrency`, `test_job_retry_backoff_and_dead_letter`, `test_reaper_requeues_stale_running_jobs`, `test_cron_enqueue_is_deduplicated`, `test_handler_runs_in_business_transaction` pass.

### P1-08 · Domain events
Kind: PRE · Tier: A · Module: M00 · Size: M · Depends on: P1-07
Read first: [07 Domain events](../07-workflow-engine.md#domain-events)
Done when:
- [ ] `test_publish_creates_delivery_per_subscriber`, `test_event_not_published_when_business_tx_rolls_back`, `test_failed_delivery_retried_independently_per_subscriber` pass.

### P1-09 · Outbound HTTP, idempotency, rate limiting
Kind: PRE · Tier: A · Module: M00 · Size: M · Depends on: P1-05
Read first: [06 Egress](../06-security-and-privacy.md#egress), [M00](../modules/M00-platform.md)
Done when:
- [ ] `test_http_client_blocks_private_and_metadata_ips`, `test_http_client_blocks_non_allowlisted_host`, `test_idempotency_returns_original_response_and_rejects_changed_body`, `test_rate_limit_window` pass.

### P1-10 · Architecture tests
Kind: PRE · Tier: A · Module: tests · Size: M · Depends on: P1-05
Read first: [04 Module boundaries](../04-repository-and-conventions.md#module-boundaries-are-tested)
Create: `backend/tests/architecture/test_{module_imports,dependency_graph,route_permissions,rls,openapi_current,migrations}.py`.
Done when:
- [ ] Each test fails on a deliberately broken example (prove it in a temporary branch or with a fixture) and passes on the clean tree.

### P1-11 · Test toolkit
Kind: PRE · Tier: B · Module: M00 · Size: S · Depends on: P1-05
Create: `app/platform/testing/{plugin.py,factories.py,auth.py}` (`db`, `business`, `ctx_for(roles, business)`, `login_as(...)` using the fake session resolver until P2-02), truncate-between-tests logic, a frozen clock fixture.
Done when:
- [ ] A sample test in `tests/` uses every fixture and passes.

### P1-12 · Frontend shell and API client pipeline
Kind: PRE · Tier: B · Module: frontend · Size: L · Depends on: P1-01, P1-05
Read first: [09](../09-frontend.md)
Create: `src/app/{router.tsx,providers.tsx,layout.tsx,theme.css,error-boundary.tsx}`, `src/api/{client.ts,schema.d.ts}`, `src/lib/{dates.ts,money.ts,problem.ts,permissions.ts}`, `src/components/common/*` (PageHeader, DataTable, StatusBadge, MoneyText, LocalTime, ConfirmDialog, ConflictDialog, EmptyState, PermissionGate), `src/locales/en.json`.
Steps: `make gen-api` runs the backend's `create_app().openapi()` → `backend/openapi.json` → `openapi-typescript` → `src/api/schema.d.ts`. The layout has sidebar navigation driven by a permissions list (stub until P2-08).
Done when:
- [ ] `pnpm test` (component tests for the common components) and `pnpm build` pass; the bundle stays under budget ([09](../09-frontend.md#accessibility-and-quality-bar-production-level)).

### P1-13 · `make check` and local CI
Kind: PRE · Tier: B · Module: repo · Size: S · Depends on: P1-10, P1-12
Steps: `make check` = `ruff check`, `ruff format --check`, `mypy`, `pytest` (architecture + unit + integration) with coverage report, `pnpm lint`, `pnpm typecheck`, `pnpm test`, licence check (fails on GPL or AGPL in the dependency tree), `gitleaks detect` if installed (otherwise print "skipped").
Done when:
- [ ] `make check` passes from a clean clone with only `make dev` running.

## Exit gate P1
- [ ] `make dev` and `make check` pass on the owner's machine.
- [ ] The architecture tests demonstrably catch a forbidden import and a route without a permission.
- [ ] Tier-A review of `platform/` done (second model review recorded).
- [ ] `PRE_EVENT_WORK.md` lists P1 tasks with dates.
