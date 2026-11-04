# 04 · Repository layout and conventions

This document is prescriptive. When it says "must", an agent must follow it exactly. The code patterns below are the reference implementation style. Copy their shape rather than inventing a new one.

## Repository tree

```
/
├── AGENTS.md                      Rules for coding agents (see 12-agent-playbook.md)
├── README.md                      Entry point for humans
├── PRE_EVENT_WORK.md              Dated record of work done before kickoff
├── LICENSE                        MIT
├── Makefile                       The single command entry point
├── .env.example                   Every environment variable, with safe dummy values
├── docs/roadmap/                  This roadmap (single source of truth)
├── data/                          Synthetic demo dataset + validator
├── backend/
│   ├── pyproject.toml             Dependencies, ruff, mypy, pytest config
│   ├── uv.lock
│   ├── alembic.ini
│   ├── openapi.json               Generated; committed; checked by a test
│   ├── migrations/
│   │   ├── env.py
│   │   ├── helpers.py             enable_business_rls(), create_updated_at_trigger()
│   │   └── versions/              YYYYMMDD_HHMM_<module>_<what>.py
│   ├── app/
│   │   ├── main.py                create_app(): composition root for HTTP
│   │   ├── worker.py              composition root for the worker loop
│   │   ├── cli.py                 `python -m app.cli seed|bootstrap-business|...`
│   │   ├── platform/              M00 kernel (no imports from app.modules!)
│   │   │   ├── config.py  db.py  context.py  permissions.py  registry.py
│   │   │   ├── audit.py   jobs.py  events.py  errors.py  http.py
│   │   │   ├── ids.py     clock.py money.py  pagination.py  logging.py
│   │   │   ├── models.py          Base, mixins, platform-owned tables
│   │   │   └── testing/           pytest fixtures, factories, login helpers
│   │   └── modules/
│   │       ├── dependencies.py    The declared module dependency graph
│   │       ├── crm/               (every module has the same shape, see below)
│   │       └── …
│   └── tests/
│       ├── architecture/          Boundary, route-permission, RLS, OpenAPI tests
│       └── integration/           Cross-module flows (booking end-to-end, etc.)
├── frontend/
│   ├── package.json  pnpm-lock.yaml  vite.config.ts  tsconfig.json  eslint.config.js
│   ├── src/
│   │   ├── app/                   Router, providers, layout, auth guard, error boundary
│   │   ├── api/                   schema.d.ts (generated) + client.ts (thin wrapper)
│   │   ├── components/ui/         shadcn/ui components (copied source)
│   │   ├── components/common/     App-wide composites (DataTable, PageHeader, MoneyText…)
│   │   ├── features/<module>/     Pages, components, hooks, routes for one module
│   │   ├── lib/                   dates.ts money.ts permissions.ts problem.ts
│   │   ├── locales/en.json
│   │   └── public-widget/         Entry for the public chat/booking pages
│   └── e2e/                       Playwright tests
├── deploy/
│   ├── docker/                    api.Dockerfile, web.Dockerfile, backup.Dockerfile
│   ├── compose/                   compose.dev.yml, compose.prod.yml, Caddyfile, Caddyfile.dev
│   ├── keycloak/                  realm-smb.json (realm as code), theme/ (later)
│   ├── terraform/
│   │   ├── modules/vultr-single-vm/
│   │   └── envs/demo/             Our hackathon deployment (state stays local, gitignored)
│   ├── cloud-init/                user-data.yaml.tftpl
│   ├── config/                    ai-routes.example.yaml, egress-allowlist.example.txt
│   └── scripts/                   install.sh upgrade.sh backup.sh restore.sh smoke.sh
└── evals/                         EVENT phase: AI evaluation cases and runner
```

The existing empty `ui/`, `scripts/` and `tests/` folders at the root are not used. Task P1-01 deletes them only after the owner confirms.

## Module template (backend)

Every module directory looks like this. Files marked *(optional)* exist only when needed.

```
app/modules/<name>/
├── __init__.py          Contains only: from .registration import register
├── README.md            Module card: purpose, tables, public API, events, permissions (kept current)
├── registration.py      def register(reg: Registry) -> None
├── public.py            The ONLY import surface for other modules
├── permissions.py       Permission constants
├── models.py            SQLAlchemy models (tables this module owns)
├── schemas.py           Pydantic DTOs (API in/out, public DTOs)
├── repository.py        All SQL for the module
├── service.py           Business rules (split into service_<area>.py if > 400 lines)
├── api.py               FastAPI router (split into api_<area>.py if > 400 lines)
├── executors.py         (optional) approved-action executors
├── jobs.py              (optional) job handlers
├── events.py            (optional) published event types + subscribed handlers
└── tests/
    ├── factories.py
    ├── test_service.py
    └── test_api.py
```

**Size limits** (keep a module understandable in one sitting): files ≤ 400 lines, functions ≤ 60 lines, module ≤ about 6,000 lines excluding tests. When a module grows past that, split it into sub-packages (for example `scheduling/availability/`). Never split it by creating a new top-level module without an ADR.

## Module boundaries are tested

`backend/tests/architecture/` contains these tests, which run in `make check`:

| Test | Fails when |
|---|---|
| `test_module_imports.py` | A module imports `app.modules.<other>.<anything other than public>`, imports a module not declared in `dependencies.py`, or `app.platform` imports `app.modules` |
| `test_dependency_graph.py` | `dependencies.py` contains a cycle |
| `test_route_permissions.py` | Any route lacks a `require(...)` dependency and is not in the explicit `PUBLIC_ROUTES` allowlist |
| `test_rls.py` | A table with a `business_id` column lacks `ROW LEVEL SECURITY` + `FORCE` + a policy |
| `test_openapi_current.py` | `backend/openapi.json` differs from `create_app().openapi()` (run `make gen-api`) |
| `test_migrations.py` | `alembic upgrade head` → `downgrade -1` → `upgrade head` fails on an empty database |

The imports test parses Python files with `ast`, so it needs no extra dependency. **Test files** (`*/tests/*`) may also import any module's `public.py` or `testing.py` to build fixtures; they may never import another module's internals.

Declare endpoint errors with `responses=error_responses(401, 403, 404, 409, 422)` from `app.platform.errors`.

## Code patterns (copy these shapes)

### Context and transactions (`platform/context.py`, `platform/db.py`)

```python
@dataclass(frozen=True, slots=True)
class Context:
    request_id: str
    actor_kind: Literal["user", "assistant", "system", "visitor"]
    user_id: UUID | None
    business_id: UUID | None
    permissions: frozenset[str]
    session_id: UUID | None = None
    ai_run_id: UUID | None = None   # set when an assistant tool acts on behalf of a user

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise Forbidden("auth.permission_denied", detail=permission)


@contextmanager
def transaction(ctx: Context) -> Iterator[Session]:
    """One database transaction with row-level-security variables set. Commits on exit."""
    with SessionLocal() as db, db.begin():
        db.execute(
            text("select set_config('app.business_id', :b, true), set_config('app.user_id', :u, true)"),
            {"b": str(ctx.business_id or ""), "u": str(ctx.user_id or "")},
        )
        yield db
```

**Rule:** the transaction is opened **in the endpoint** (or job handler), never inside a service. Services receive `db` and `ctx` and never commit. The endpoint builds its response *inside* the `with` block, so a failed commit can never produce a `200` response.

### Model (`models.py`)

```python
class Customer(BusinessScopedBase):          # adds id (UUIDv7), business_id, version, created_at, updated_at
    __tablename__ = "customers"
    kind: Mapped[str] = mapped_column(String(16))                 # 'person' | 'organization'
    display_name: Mapped[str] = mapped_column(String(200))
    primary_email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(16), default="active")
    __table_args__ = (
        UniqueConstraint("business_id", "id"),                    # target for composite FKs
        CheckConstraint("kind in ('person','organization')", name="kind"),
    )
```

### Repository (`repository.py`)

```python
def get(db: Session, ctx: Context, customer_id: UUID) -> Customer | None:
    # Always filter by business explicitly, even though row-level security also applies.
    return db.scalar(select(Customer).where(Customer.business_id == ctx.business_id, Customer.id == customer_id))


def update_versioned(db: Session, ctx: Context, customer_id: UUID, expected_version: int, changes: dict[str, Any]) -> Customer | None:
    stmt = (
        update(Customer)
        .where(Customer.business_id == ctx.business_id, Customer.id == customer_id, Customer.version == expected_version)
        .values(**changes, version=Customer.version + 1, updated_at=func.now())
        .returning(Customer)
    )
    return db.scalar(stmt)   # None means: not found OR version conflict → service decides which
```

### Service (`service.py`)

```python
def update_customer(db: Session, ctx: Context, customer_id: UUID, data: CustomerUpdate) -> Customer:
    ctx.require(P.CUSTOMER_WRITE)                       # services re-check: executors and AI tools call them too
    before = repository.get(db, ctx, customer_id)
    if before is None:
        raise NotFound("crm.customer_not_found")
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    after = repository.update_versioned(db, ctx, customer_id, data.version, changes)
    if after is None:
        raise Conflict("crm.customer_version_conflict", current_version=before.version)
    audit.record(db, ctx, action="crm.customer.updated", target_type="customer", target_id=after.id,
                 changes=audit.diff(before, after, redact=REDACTED_FIELDS))
    return after
```

### API (`api.py`)

```python
router = APIRouter()

@router.patch("/customers/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: UUID, body: CustomerUpdate, ctx: Context = Depends(require(P.CUSTOMER_WRITE))) -> CustomerOut:
    with transaction(ctx) as db:
        customer = service.update_customer(db, ctx, customer_id, body)
        return CustomerOut.model_validate(customer)
```

`require(permission)` (from `platform/permissions.py`) loads the session from the cookie, verifies CSRF for unsafe methods, resolves the active business and effective permissions, checks the permission and returns a `Context`.

### Public surface (`public.py`)

```python
"""Public API of the crm module. Other modules may import ONLY from this file."""
from .schemas import CustomerSummary, TimelineEntryIn
from .service import add_timeline_entry, find_customer_candidates, get_customer_summary

__all__ = ["CustomerSummary", "TimelineEntryIn", "add_timeline_entry", "find_customer_candidates", "get_customer_summary"]
```

Public functions take `(db, ctx, ...)` and return DTOs (Pydantic models or frozen dataclasses), **never ORM objects**. Returning ORM objects would leak the table structure.

### Registration (`registration.py`)

```python
def register(reg: Registry) -> None:
    reg.add_router(api.router, prefix="/crm", tags=["crm"])
    reg.add_permissions(P.ALL)
    reg.add_executor("crm.create_customer", executors.create_customer, payload_model=CreateCustomerPayload)
    reg.subscribe("scheduling.appointment_confirmed", events.on_appointment_confirmed)  # only if allowed by the graph
```

`platform/registry.py` holds routers, permissions, executors, job handlers and subscribers. `approvals` looks executors up in the platform registry, so it never imports business modules.

## API conventions

| Topic | Rule |
|---|---|
| Base path | `/api/v1/<module>/<resource>`. Public visitor endpoints: `/api/v1/public/{business_slug}/…` |
| Names | Plural nouns, kebab-case paths (`/stock-movements`), snake_case JSON fields |
| Methods | `GET` list/read, `POST` create or command (`POST /appointments/{id}:cancel` is **not** used; use `POST /appointments/{id}/cancel`), `PATCH` partial update, `DELETE` archive (soft) |
| Concurrency | Every updatable resource returns `version`. `PATCH` and commands require `version`. A mismatch returns `409` with `current_version` |
| Idempotency | Creating POSTs accept `Idempotency-Key`. The same key and body within 24 h return the original response (`platform.idempotency_keys` table) |
| Pagination | `?limit=50&cursor=…` → `{"items": [...], "next_cursor": "…" or null}`. Maximum limit 200 |
| Filtering | Explicit query parameters (`?status=open&assigned_to=me`). No generic filter language in the URL |
| Time | Request and response timestamps are ISO 8601 UTC (`2026-11-10T16:00:00Z`). Local display is a frontend job, using the business or location `timezone` |
| Money | `{"amount_minor": 4500, "currency": "USD"}` |
| Errors | RFC 9457 problem details: `{"type": "https://smb.local/problems/<code>", "title": "...", "status": 409, "code": "crm.customer_version_conflict", "detail": "...", "request_id": "...", "errors": [{"field": "email", "code": "invalid"}]}` |
| Status codes | 200, 201 (created), 202 (accepted for async), 204, 400, 401, 403, 404, 409, 422 (validation), 429, 500, 503 |
| Error codes | `<module>.<snake_case_reason>`, listed in the module spec |
| Auth | Session cookie + `X-CSRF-Token` header on POST/PATCH/PUT/DELETE |
| OpenAPI | Every endpoint has `summary`, a `response_model` and its possible error codes in `responses=` |

## Testing conventions

- **Backend tests use real PostgreSQL** (the `db-test` service in `compose.dev.yml`), never SQLite. The schema is created once per test session with `alembic upgrade head`, and tables are truncated between tests.
- Fixtures in `app/platform/testing/`: `db`, `business` (with time zone), `login_as(role, business)`, which returns a `TestClient` with session cookie and CSRF header, and `ctx_for(role, business)` for service tests.
- Each module tests: every service rule, every permission (allowed and denied roles), cross-business denial (business B cannot see business A's row, even with a guessed ID) and version conflicts.
- Test names state behaviour: `test_booking_overlapping_slot_returns_conflict`.
- Frontend: component tests with MSW handlers built from the generated types. Playwright end-to-end tests against the dev Compose stack with seeded data.
- Coverage target: services ≥ 90% lines, overall backend ≥ 80%. Do not chase 100%.

## Migrations

- One linear Alembic history. Files are named `YYYYMMDD_HHMM_<module>_<what>.py`. Write migrations by hand and use autogenerate only as a cross-check.
- Every new business table calls `enable_business_rls("<table>")`, `create_updated_at_trigger("<table>")` and, for ledgers, `append_only("<table>")` from `migrations/helpers.py`. Migration files use the explicit revision ID `YYYYMMDD_HHMM_<module>_<what>` (see the two platform migrations as examples).
- Every migration has a working `downgrade()`.
- **Expand and contract:** never rename or drop a column in the same release that stops using it. Add, migrate data, switch code, then remove in a later release. This keeps upgrades of customer installations safe.

## Frontend conventions (details in [09](09-frontend.md))

- One folder per module in `src/features/<module>/` with `routes.tsx`, `pages/`, `components/`, `hooks.ts` (TanStack Query hooks wrapping the generated client) and a `README.md`.
- Features never import from other features. Shared code moves to `components/common` or `lib`.
- Never hand-write API types. Run `make gen-api` after any backend schema change.
- All user-visible strings come from `locales/en.json`.

## Environment variables (`.env.example`)

```
APP_ENV=dev                                  # dev | test | prod
APP_BASE_URL=https://localhost
APP_SECRET_KEY=change-me-64-random-bytes     # signs CSRF/visitor tokens
DATABASE_URL=postgresql+psycopg://smb_app:dev@postgres:5432/smb          # app role, row-level security applies
DATABASE_MIGRATION_URL=postgresql+psycopg://smb_owner:dev@postgres:5432/smb  # owner role, migrations only
SESSION_TTL_HOURS=12
SESSION_IDLE_MINUTES=60
OIDC_ISSUER=https://localhost/auth/realms/smb
OIDC_CLIENT_ID=smb-app
OIDC_CLIENT_SECRET=dev-only
KEYCLOAK_ADMIN_CLIENT_ID=smb-admin           # service account: manage-users only
KEYCLOAK_ADMIN_CLIENT_SECRET=dev-only
SMTP_URL=                                    # empty = simulated delivery only
OBJECT_STORAGE_ENDPOINT=https://sjc1.vultrobjects.com
OBJECT_STORAGE_BUCKET=smb-files
OBJECT_STORAGE_ACCESS_KEY=
OBJECT_STORAGE_SECRET_KEY=
RESTIC_REPOSITORY=s3:https://sjc1.vultrobjects.com/smb-backups
RESTIC_PASSWORD=                             # customer keeps a copy offline
AI_ROUTES_FILE=/config/ai-routes.yaml
VULTR_INFERENCE_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
EGRESS_ALLOWLIST=api.vultrinference.com,sjc1.vultrobjects.com
AI_DAILY_TOKEN_BUDGET_PER_BUSINESS=2000000
LOG_LEVEL=info
```

Secrets are never committed. `.env` is gitignored, and production values are generated by `install.sh` on the operator's machine.

## Makefile targets (contract)

| Target | Does |
|---|---|
| `make dev` | Start the Compose dev stack (postgres, keycloak, api and worker with reload, vite, caddy) |
| `make seed` | Load `data/fixtures.json` into the dev database through services (not raw SQL) |
| `make check` | Lint (ruff, oxlint), format check, type check (mypy strict, tsc), architecture tests, unit and integration tests, frontend tests, licence check, gitleaks secret scan |
| `make test` | Backend and frontend tests only |
| `make gen-api` | Regenerate `backend/openapi.json` and `frontend/src/api/schema.d.ts` |
| `make migrate` / `make migration name=…` | Apply migrations / create an empty migration file |
| `make e2e` | Playwright against a fresh stack with the seed |
| `make eval model_class=agent` | EVENT phase: run the AI evaluation set |
| `make images` | Build production images with a version tag |
| `make backup-test` | Back up the dev database, restore it into a scratch database and compare row counts |

**Reload in the dev stack.** The `api` (uvicorn `--reload`) and the `worker` (`python -m app.worker` run by `watchfiles`) restart
themselves when Python files change (the worker watches `backend/app`), so new routes, jobs, executors and event subscribers
need no manual restart; `worker_started` appears in `make dev-logs` a couple of seconds after saving. The worker gets SIGINT,
finishes the jobs it is running and exits. It waits about 30 s; a longer job is abandoned and runs again after its five-minute
lease expires. A `KeyboardInterrupt` traceback right after a reload only means that a second change arrived while the new worker
was still importing; it had not claimed anything yet. Only `*.py` files trigger a reload. After changing a non-Python file the
worker reads at start-up (a template or a YAML file under `deploy/config`), run
`docker compose -p smb -f deploy/compose/compose.dev.yml --env-file .env restart worker`; after changing `.env`, use `up -d worker`
instead, which recreates it with the new environment. After adding a migration run `make migrate`.

## Commits and branches (once the owner allows commits)

- Conventional Commits that include the task ID: `feat(crm): customer merge preview [P3-04]`.
- One task per commit where possible. Never rewrite pushed history. Never commit `.env`, Terraform state or real data.
