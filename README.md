# SMB Operations Suite — AI-assisted ERP for small and mid-size businesses

**Status (2026-11-03 UTC): the non-AI ERP is built and tested locally; no AI functionality, no deployment and no commits yet.** Everything here is disclosed pre-event preparation for the Vultr "Agent Rush" hackathon (lablab.ai), with synthetic data only. See [PRE_EVENT_WORK.md](PRE_EVENT_WORK.md). The judge-facing README is drafted in [docs/submission/](docs/submission/README-judges.draft.md) and replaces this file at submission.

## Start here

| You want to… | Read |
|---|---|
| Understand the product and build plan | [docs/roadmap/README.md](docs/roadmap/README.md) — the single source of truth for development |
| Pick up a build task (human or coding agent) | [docs/roadmap/12-agent-playbook.md](docs/roadmap/12-agent-playbook.md), then [docs/roadmap/phases/README.md](docs/roadmap/phases/README.md) |
| See what was prepared before the event, and when | [PRE_EVENT_WORK.md](PRE_EVENT_WORK.md) |
| Understand repository rules for agents | [AGENTS.md](AGENTS.md) |

## Folder map

| Path | Contents | State |
|---|---|---|
| `docs/roadmap/` | Implementation roadmap: architecture, module specs, phased tasks | Current |
| `data/` | Synthetic demo dataset (`fixtures.json`), portable data-contract schema (`schema.sql`), validator | Current, synthetic only |
| `design-review-cloud-20261103/` | Imported cloud design documents and their review | Historical input to the roadmap |
| `docs/SCOPE.md`, `docs/ARCHITECTURE.md`, `docs/KICKOFF_PLAN.md`, `docs/SOURCES.md` | Earliest local drafts | Superseded, kept for provenance |
| `docs/runbooks/` | Install, upgrade, backup and restore, secrets, incident, access, teardown | Written 2026-11-03; Vultr steps unverified until the first deployment |
| `backend/`, `frontend/`, `deploy/` | Application code and deployment files | Built through phase P4 (deployment files tested locally only) |

## What you can run today

Prerequisites: Docker, `uv`, Node 24 + pnpm (Corepack), GNU Make.

```sh
make dev           # dev stack on https://localhost:8443 (accept the local certificate warning)
make seed reset=1  # recreate the two synthetic demo businesses in the dev stack
make demo-logins   # Keycloak logins for the demo staff (Morgan, Riley, ...); passwords go to .demo-logins/ (mode 600), never to the screen
make check         # everything that must pass before a task is done
make e2e           # Playwright end-to-end tests against the running dev stack (resets the demo data)
make images        # production images (smb-api, smb-web, smb-backup); VERSION=0.1.0 REGISTRY=host/ns PLATFORM=linux/amd64
make rehearse      # local production rehearsal: images, smoke, e2e, upgrade, rollback, restore (stop `make dev` first)
make backup-test   # backup and restore round trip on a throwaway stack
make help          # all commands
```

Synthetic data check (no dependencies): `python3 -I data/validate_fixtures.py`.

Status: phases P1–P4 (platform kernel, core services, business modules without AI, packaging and deployment files), plus marketing, the System page and data export/erasure, are built locally; nothing is deployed to Vultr; see [docs/roadmap/phases/STATUS.md](docs/roadmap/phases/STATUS.md). Nothing is committed yet.
