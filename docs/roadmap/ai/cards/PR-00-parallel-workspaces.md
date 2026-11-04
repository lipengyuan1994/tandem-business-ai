# PR-00 · Parallel agent workspaces (one test database per agent)

Kind **PRE** (tooling, no AI) · Tier B · Size S · Depends on: — · Blocks: parallel work on every other card

## Goal
Two or three coding agents must be able to run the backend test suite at the same time without breaking each other.
Today every run uses the one test database `localhost:5433/smb`, and tests truncate tables, so concurrent runs fail
randomly. Give each agent its own database in the same `db-test` container.

## Context pack (read in this order; nothing else)
1. `deploy/compose/postgres-init.sh` (whole file, ~40 lines): roles and grants for database `smb`.
2. `deploy/compose/compose.dev.yml`, service `db-test` only.
3. `backend/app/platform/testing/plugin.py` lines 1–60: `TEST_DATABASE_URL`, `TEST_DATABASE_MIGRATION_URL`.
4. `Makefile`, the `test`, `test-db` and `help` targets.

## You build
- `deploy/scripts/test-db.sh` with `new <name>` and `drop <name>`: creates/drops database `smb_<name>` in the running
  `db-test` container (via `docker compose ... exec -T db-test psql -U postgres`), applying the same grants as
  `postgres-init.sh` gives database `smb` (CONNECT to smb_app without TEMPORARY, schema usage, default privileges for
  tables, sequences, functions owned by smb_owner). `<name>` must match `^[a-z0-9_]{1,20}$`. Prints the two `export`
  lines to use. Idempotent (`new` on an existing database only prints the exports).
- Makefile targets `test-db-new name=<n>` and `test-db-drop name=<n>` calling the script; listed in `make help`.
- A short section "Parallel agents" in `docs/roadmap/ai/README.md` is already written; add the exact commands to
  `docs/roadmap/11-quality-and-operations.md` under the test strategy.

## Tests / checks
- `shellcheck deploy/scripts/test-db.sh` and `bash -n`.
- Manual: `make test-db-new name=w2`; then in two terminals run the full backend suite, one with the default database and
  one with `TEST_DATABASE_URL=... TEST_DATABASE_MIGRATION_URL=...` from the printed exports. Both pass.
- `make test-db-drop name=w2` removes it; dropping `smb` itself is refused.

## Done when
`make check` passes; the manual concurrent run above passes and its output summary is in your report.

## Out of scope
New containers, CI changes, anything touching application code.
