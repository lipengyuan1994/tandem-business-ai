# 11 · Quality and operations

## Test strategy

| Layer | Scope | Tool | Runs in |
|---|---|---|---|
| Architecture | Module boundaries, route permissions, row-level security coverage, OpenAPI freshness, migrations up/down | pytest + `ast` | `make check` |
| Unit / service | Business rules per module, against real Postgres | pytest | `make check` |
| API | Each endpoint: success, 401, 403 per denied role, 404 cross-business, 409, 422 | pytest + FastAPI `TestClient` | `make check` |
| Integration | Cross-module flows (book with parts, cancel releases stock, proposal → execution → outbox) | pytest in `backend/tests/integration` | `make check` |
| Concurrency | Double booking, double approval, two workers claiming one job, stock oversell | pytest with threads and separate connections | `make check` |
| Frontend component | Pages and forms | Vitest + MSW | `make check` |
| End to end | Flagship workflow, login, conflict, accessibility | Playwright on the compose stack | `make e2e` (before each phase gate) |
| AI evals | Intake, copilot, injection, time zones | `evals/runner.py` | `make eval` (EVENT; on prompt, tool or route changes) |
| Deployment smoke | Health, login page, API readiness, backup dry-run | `deploy/scripts/smoke.sh` | After every deploy |
| Backup restore | Dump → restore → compare | `make backup-test` | Weekly + before releases |
| Security baseline | Dependency audit (`pip-audit`, `pnpm audit`), image scan (`trivy` if installed), OWASP ZAP baseline on demo URL | — | P6 and P8 gates |
| Load (v1) | 50 concurrent staff users, 5 req/s each, p95 < 300 ms for reads | `k6` or `locust` | P8 gate |

**Rule:** a bug fix starts with a failing test that reproduces it.

### Parallel agents

Use a separate git worktree and test database for each agent: backend tests truncate tables, so two suites must
never share a database. Start the shared throwaway service once, then create a database per additional agent:

```sh
make test-db
make test-db-new name=w2
```

The second command prints these exports (using the synthetic local-test credentials):

```sh
export TEST_DATABASE_URL='postgresql+psycopg://smb_app:smb_app_dev@localhost:5433/smb_w2'
export TEST_DATABASE_MIGRATION_URL='postgresql+psycopg://smb_owner:smb_owner_dev@localhost:5433/smb_w2'
```

In the first agent's terminal, leave both variables unset to use the default `smb` database. In the second agent's
terminal, paste both exports above. From each worktree, run the full backend suite concurrently:

```sh
cd backend
.venv/bin/python -m pytest -q -p no:cacheprovider
```

Repeat with `name=w3` for a third agent and use its printed exports. Names must match `^[a-z0-9_]{1,20}$`;
the script always prefixes them with `smb_`, so it cannot drop the default `smb` database. Creating an existing
database only prints the exports. The helper expects the existing `db-test` service to be running and does not
start or restart containers. If local test passwords differ from the defaults, adjust the exported URLs locally.

After that agent's suite has stopped, remove its database from the repository root:

```sh
make test-db-drop name=w2
unset TEST_DATABASE_URL TEST_DATABASE_MIGRATION_URL
```

## Definition of done (every task)

- [ ] Code follows [04](04-repository-and-conventions.md); files and functions are within size limits.
- [ ] New or changed behaviour has tests, including permission-denied and cross-business cases where relevant.
- [ ] `make check` passes locally, and the output summary is pasted into the task report.
- [ ] If the API changed: `make gen-api` was run and frontend types compile.
- [ ] If the schema changed: a migration with a working downgrade and row-level security on new tables.
- [ ] Every mutation writes an audit event.
- [ ] The module `README.md` card and the module spec in `docs/roadmap/modules/` match the code (tables, public functions, events, permissions).
- [ ] `docs/roadmap/phases/STATUS.md` is updated. Any PRE-kind work is noted in `PRE_EVENT_WORK.md` before kickoff.
- [ ] No secrets, real personal data or TODOs without a task ID.

## Phase gates

Each phase file ends with an **exit gate**: a list of commands and checks the owner (or a Tier-A reviewer model) runs before the next phase begins. A phase is not done because its tasks are ticked. It is done when its gate passes.

## Observability

| Signal | Implementation |
|---|---|
| Logs | `structlog` JSON to stdout, captured by Docker (10 MB × 5). Fields: `ts, level, event, request_id, business_id, user_id, module, duration_ms`. Never bodies, emails, phones or prompts |
| Metrics | `prometheus-client` on an internal port (not routed by Caddy): request count and latency by route, job queue depth and age, job failures, outbox status counts, AI runs, tokens, cost, errors, DB pool usage |
| Health | `/healthz` (process up), `/readyz` (DB reachable, migrations at head, Keycloak reachable) |
| System page (M18) | Version, uptime, queue age, dead jobs, outbox `unknown` count, last backup age and result, certificate expiry, disk usage, AI budget usage |
| Alerts | In-app and email (when SMTP is configured) to owners and admins: backup older than 26 h, dead jobs, disk > 80 %, certificate < 14 days, AI budget 80 % / 100 % |
| Uptime from outside | Customer's choice of external monitor (documented, not bundled) |

## Release and versioning

- Semver. `0.x` until the P8 production gate, then `1.0.0`.
- `CHANGELOG.md` per release, with a section "Upgrade notes" (migrations, config changes).
- Database migrations are always forward-compatible for one release (expand/contract).
- Each release is tested by upgrading the demo environment from the previous release with seeded data, not only by fresh install.

## Runbooks (written in P4/P8 under `docs/runbooks/`)

| Runbook | Covers |
|---|---|
| `install.md` | Running `install.sh`, DNS, first login, MFA setup |
| `upgrade.md` | `upgrade.sh`, rollback |
| `backup-restore.md` | Manual backup, restore to a new database, verification, restore drill record |
| `rotate-secrets.md` | DB passwords, OIDC client secret, inference keys, restic password change |
| `incident.md` | Service down, disk full, stuck jobs, outbox `unknown`, AI provider outage (switch route or disable AI) |
| `teardown.md` | Destroy resources, verify billing, retention of last backup |
| `access.md` | SSH via temporary firewall rule, Keycloak admin through SSH tunnel |

## Production readiness checklist (P8 exit gate, before any real customer data)

**Security**
- [ ] OWASP ASVS level 2 checklist reviewed for authentication, sessions, access control, validation, logging; findings fixed or accepted in writing
- [ ] ZAP baseline scan: no high findings
- [ ] Dependency and image scans: no unpatched critical vulnerabilities
- [ ] Row-level security and cross-business tests green; manual attempt to read another business's IDs fails
- [ ] External port scan (IPv4 and IPv6) shows only 80/443 (+22 if allowed)
- [ ] Secrets rotation tested once

**Reliability**
- [ ] Point-in-time recovery (pgBackRest) configured; restore drill done with recovery time measured and recorded
- [ ] Upgrade from previous version tested with data
- [ ] Load test meets targets on `vhp-2c-4gb-amd` and `vhp-4c-8gb-amd`
- [ ] Alerting verified by forcing each alert once

**Product**
- [ ] Accessibility: axe clean on all main pages; keyboard-only run of the flagship workflow
- [ ] AI evals at or above thresholds on the selected routes; injection suite 100 %
- [ ] Data export and erasure work end to end
- [ ] Runbooks complete; owner can install a fresh customer in under 30 minutes by following `install.md`

**Legal and business** (owner)
- [ ] Privacy notice and data processing terms reviewed by a professional
- [ ] Model licences and provider terms checked for commercial use
- [ ] Support and on-call responsibility named for each pilot customer
