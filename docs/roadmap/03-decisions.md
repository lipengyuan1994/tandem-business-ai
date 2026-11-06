# 03 · Decisions (ADRs)

Each decision is binding until a new ADR replaces it. **Agents must not add a framework, library or service that contradicts these decisions.** To propose a change, add an ADR with status `PROPOSED` and ask the owner.

Format: Status · Decision · Why · Rejected alternatives · Consequences / revisit when.

---

## ADR-001 Modular monolith
- **Status:** ACCEPTED 2026-11-03
- **Decision:** One backend codebase with strictly bounded modules ([02](02-architecture.md)), deployed as two processes (`api`, `worker`) from one image.
- **Why:** Multi-module actions (appointment + stock reservation + audit + outbox) need one database transaction. SMB customers need one simple deployable. Strict boundaries let a model work on one module without loading the rest.
- **Rejected:** Microservices (distributed transactions, much more operations work); an unstructured monolith (becomes too big for any context window).
- **Revisit when:** a module needs independent scaling or a separate release cycle.

## ADR-002 Backend stack: Python 3.13, FastAPI, SQLAlchemy 2 (sync), Pydantic v2, Alembic, uv
- **Status:** ACCEPTED 2026-11-03
- **Decision:** Python 3.13. FastAPI with **synchronous** endpoints and SQLAlchemy 2.0 ORM using `psycopg` (v3). Pydantic v2 for schemas and settings (`pydantic-settings`). Alembic for migrations. `uv` for dependencies and lockfile. `ruff` for lint and format, `mypy --strict` for types, `pytest` for tests. `structlog` for logs. `httpx` for outbound HTTP.
- **Why:** Python has the best AI ecosystem, and FastAPI generates OpenAPI automatically. Synchronous SQLAlchemy avoids the most common async mistakes lower-end models make (lazy loads in async sessions, forgotten awaits). FastAPI runs sync endpoints in a thread pool, which is plenty for SMB traffic. Inference calls run in the worker, not in request threads, except streaming chat ([08](08-ai-layer.md)).
- **Rejected:** Node/NestJS (weaker AI and data tooling for this team); async SQLAlchemy (error-prone); Django (heavier ORM conventions, and its admin and auth overlap with Keycloak).
- **Consequences:** Pin exact versions in `uv.lock` during P1-01. Upgrade dependencies only in dedicated tasks.
- **As built (P1, 2026-11-03):** Python 3.13.14, FastAPI 0.162, SQLAlchemy 2.1, Pydantic 2.14, psycopg 3.3. FastAPI ≥ 0.16x wraps included routers lazily, so code that walks routes must use `fastapi.routing.iter_route_contexts` (see `tests/architecture/test_route_permissions.py`).

## ADR-003 PostgreSQL 17, one database, row-level security
- **Status:** ACCEPTED 2026-11-03
- **Decision:** PostgreSQL 17 using the `pgvector/pgvector:pg17` image, so the vector extension is available without a later image change. Extensions: `btree_gist` (no-overlap constraints for appointments) and `vector` (from P5, optional). One application database, plus a separate database `keycloak` on the same server. Row-level security on every business-scoped table ([05](05-data-model.md)).
- **Why:** Exclusion constraints make double-booking impossible at the database level. `SELECT … FOR UPDATE SKIP LOCKED` gives a reliable job queue without Redis. Row-level security protects against a forgotten `WHERE business_id = …`.
- **Rejected:** MySQL (no exclusion constraints, weaker row-level security); SQLite (no concurrency guarantees for production); a separate vector database (unnecessary at SMB scale).
- **Revisit when:** a customer wants Vultr Managed PostgreSQL. Check that its extension list includes `btree_gist` and `vector` before offering it.

## ADR-004 Keycloak for authentication; the application owns authorisation
- **Status:** ACCEPTED 2026-11-03
- **Decision:** A self-hosted Keycloak container (pinned version) in every deployment provides login, password policy, TOTP multi-factor authentication, brute-force protection and password reset. The backend acts as a *backend-for-frontend*. It runs the OIDC authorisation-code flow with PKCE using `authlib`, stores sessions server-side in Postgres and gives the browser only an HttpOnly session cookie. Roles and permissions per business live in the application database (`identity` module), not in Keycloak.
- **Why:** The design review required "no ad hoc password system". Keycloak is mature, Apache-2.0 licensed and self-hostable, so login data stays in the customer's deployment. Keeping authorisation in the app keeps the permission matrix testable and close to the code.
- **Rejected:** Writing our own password and MFA handling (security risk); hosted identity providers such as Auth0 or Clerk (a third-party privacy dependency for every customer, plus cost); Ory Kratos and Zitadel (fewer examples available for lower-end models to follow).
- **As built (P2, 2026-11-03):** OIDC is implemented directly with `httpx` + `joserfc` (authlib's JOSE module is deprecated). Back-channel calls go only to `OIDC_INTERNAL_ISSUER_URL`. The CSRF token is stable per session: HMAC(server secret, session id). Keycloak 26.8.1; required actions run password change first, then TOTP setup.
- **Consequences:** Keycloak adds about 0.7 GB of RAM, so the minimum VM is 4 GB. Realm configuration is code (`deploy/keycloak/realm-smb.json`). Without SMTP, invitations show a one-time temporary password to the inviting owner.

## ADR-005 Frontend: React + TypeScript + Vite single-page app
- **Status:** ACCEPTED 2026-11-03
- **Decision:** React 19, TypeScript (strict), Vite. TanStack Router (typed routes) and TanStack Query (server state). shadcn/ui components on Radix with Tailwind CSS v4. TanStack Table for data grids. react-hook-form + zod for forms. i18next with all strings in `en.json`. Typed API client generated from OpenAPI with `openapi-typescript` and calls made through `openapi-fetch`. Tests: Vitest + Testing Library + MSW; Playwright for end-to-end tests. Package manager: pnpm.
- **As built (P1, 2026-11-03):** React 19.3, Vite 8, TypeScript 6, Tailwind 4.3, pnpm 12. Linting uses **oxlint** (the Vite template default; `typescript/no-explicit-any` is an error) instead of ESLint. **TanStack Table is pinned to v8**, because v9 is new and lower-end models know v8 well. shadcn/ui components import the `cn` class helper from shadcn's `cn` package, and Radix comes from the unified `radix-ui` package. No `next-themes`.
- **Why:** An ERP sits behind a login, so server-side rendering adds nothing. A static SPA served by Caddy is the simplest container. Generated types stop frontend/backend drift, which is the most common integration bug in AI-written code. shadcn/ui copies component source into the repo, so styling is fully owned.
- **Rejected:** Next.js (SSR complexity and a Node runtime in production for no benefit); a component library with its own theme engine, such as MUI (heavier, harder to restyle); Redux (TanStack Query covers server state).

## ADR-006 Jobs, domain events and outbox in PostgreSQL
- **Status:** ACCEPTED 2026-11-03
- **Decision:** No Redis, Celery or message broker. Three Postgres tables: `jobs`, `domain_events` with `event_deliveries`, and `outbox_messages`. One `worker` process claims rows with `FOR UPDATE SKIP LOCKED` ([07](07-workflow-engine.md)).
- **Why:** Enqueueing in the same transaction as the business change is what makes the outbox correct. One less container for customers to run.
- **Rejected:** Celery + Redis (two-phase problem between database and broker, plus another stateful service); `procrastinate` (good, but the outbox semantics are core enough to own in about 300 lines).
- **Revisit when:** sustained load exceeds about 50 jobs per second, which is unlikely for SMBs.

## ADR-007 AI: provider-agnostic gateway, Vultr Serverless Inference by default, JSON-schema agent protocol
- **Status:** ACCEPTED 2026-11-03 (model IDs are chosen by evals in P5, not here)
- **Decision:** All model calls go through `ai.gateway`, which has two adapters: **OpenAI-compatible** (Vultr Serverless Inference at `https://api.vultrinference.com/v1`, OpenAI, Google Gemini's OpenAI-compatible endpoint, a local `llama.cpp` server) and **Anthropic Messages** (Claude Haiku). Each kind of AI job is a *model class* (`fast_extract`, `agent`, `writer`, `embed`, `stt`, `tts`, `safety`). Configuration maps each class to a provider and model, with an ordered fallback list. The agent loop uses **one structured-output protocol**: every model turn must return JSON matching `AgentStep` ([08](08-ai-layer.md#agent-protocol)). It does not rely on provider-specific tool calling.
- **Why:** Vultr's live catalog (fetched 2026-11-03, [15](14-sources.md)) has low-cost text models with tool and response-format support, embeddings, rerankers, Whisper speech-to-text and text-to-speech. That gives a Vultr-native story for the judges, and those calls are covered by the credits. A JSON protocol works the same on every provider, including small local models, so switching providers is a configuration change. Local CPU inference is unlikely to meet latency targets on affordable VMs (see [08](08-ai-layer.md#cpu-only-local-model-feasibility)). It stays an optional "strict-private" profile, decided by a benchmark (task P5-02).
- **Rejected:** LiteLLM (large dependency surface; two thin adapters are enough); LangChain and LangGraph (abstraction churn, hard for lower-end models to debug); native tool-calling only (behaviour differs per provider and model).
- **Consequences:** Hosted inference means customer text leaves the deployment. It is disclosed per business, and businesses can switch to strict-private mode.

## ADR-008 Docker Compose on a VM, not Kubernetes
- **Status:** ACCEPTED 2026-11-03
- **Decision:** Each deployment is Docker Engine + Docker Compose on one Vultr Cloud Compute VM, managed by a systemd unit.
- **Why:** SMB customers need something one person can operate. Vultr Kubernetes Engine (available, versions 1.35–1.37 listed 2026-11-03) adds a control plane, node pools and load balancer cost for no benefit at this scale.
- **Revisit when:** a hosted multi-customer offering is planned.

## ADR-009 Terraform (Vultr provider) + cloud-init installer; images in Vultr Container Registry
- **Status:** ACCEPTED 2026-11-03
- **Decision:** `deploy/terraform/modules/vultr-single-vm` creates the instance, firewall group and rules, and optional reserved IP and DNS records, using the official `vultr/vultr` provider. cloud-init installs Docker, writes configuration and starts Compose. Images are pushed to Vultr Container Registry (the free Start Up plan has 10 GB). A wrapper script `deploy/scripts/install.sh` runs Terraform with the **customer's** Vultr API key, which is used locally and never stored in our systems.
- **Why:** Repeatable installs on a customer's own account, with Vultr services throughout.
- **Rejected:** Manual console clicks (not repeatable); Ansible (adds tooling, and cloud-init is enough for one VM); Vultr Marketplace apps (worth revisiting for distribution after v1).

## ADR-010 Backups: restic to Vultr Object Storage; point-in-time recovery in v1
- **Status:** ACCEPTED 2026-11-03
- **Decision:** MVP: a `backup` container runs `pg_dump -Fc` every night and every 6 hours. restic encrypts and uploads the dumps to a Vultr Object Storage bucket (S3-compatible) and applies retention (7 daily, 4 weekly, 6 monthly). v1: add pgBackRest with WAL archiving to the same storage for point-in-time recovery. Restore is tested in CI with a throwaway database and quarterly in production.
- **Why:** Off-instance and encrypted (the restic key is held by the customer), with a simple restore. VM snapshots alone are not a database recovery plan.

## ADR-011 Identifiers, time, money, quantities
- **Status:** ACCEPTED 2026-11-03
- **Decision:** Primary keys are UUIDv7 generated in the application (`platform/ids.py`, RFC 9562). Timestamps are `timestamptz` and always UTC in the database and API (ISO 8601 with `Z`). Each business and location stores an IANA time zone, and the UI shows local time. Money is `BIGINT` minor units plus an ISO 4217 `currency` column. Quantities are `NUMERIC(14,3)` with a `unit` code, and discrete items use whole numbers by validation.
- **Why:** Sortable IDs keep indexes efficient. No floating point for money or stock. Time zone bugs are the most common scheduling defect.

## ADR-012 API style
- **Status:** ACCEPTED 2026-11-03
- **Decision:** REST and JSON under `/api/v1`. Errors use RFC 9457 `application/problem+json`. Optimistic concurrency uses a `version` field in update bodies, and a mismatch returns `409`. Lists use cursor pagination. POSTs that create things accept an `Idempotency-Key` header. Public visitor endpoints live under `/api/v1/public/{business_slug}/…`. Full rules are in [04](04-repository-and-conventions.md#api-conventions).
- **Rejected:** GraphQL (harder to permission-check per field and to cache; not needed).

## ADR-013 Build the ERP core ourselves rather than extend Odoo or ERPNext
- **Status:** ACCEPTED 2026-11-03
- **Decision:** Build a focused core (CRM, scheduling, inventory, catalog, marketing; sales and purchasing in v1).
- **Why:** The product's value is the approval-gated AI workflow across modules, which needs control of the data model and transactions. Odoo and ERPNext are large GPL/LGPL codebases with their own frameworks, which lower-end models handle poorly. The hackathon also requires original, MIT-compatible work.
- **Consequences:** We must not promise accounting or payroll. Integrate with existing accounting tools by export (v1) or connectors (later).

## ADR-014 Eligibility split: non-AI before kickoff, AI after
- **Status:** ACCEPTED 2026-11-03 — **pending the organisers' answer (task P0-02)**
- **Decision:** Before 3 Nov 2026 15:00 UTC, build only tasks marked `PRE`: platform, ERP modules, UI, deployment tooling, synthetic data. Everything marked `EVENT` is built after kickoff: inference gateway, prompts, agent loop, AI tools, evals, AI-generated content, voice. Record all PRE work, with dates, in `PRE_EVENT_WORK.md`, and keep git history honest.
- **Why:** lablab's general guide expects core AI functionality to be built during the event and generally allows prior non-AI scaffolding. A full ERP built in advance is more than "scaffolding" in a strict reading, so we ask the organisers and disclose everything.
- **If the organisers object:** the PRE work stays in the repository as disclosed prior work, and the submission is presented as "AI layer built during the event on top of a disclosed pre-existing ERP base". If they disallow even that, the owner decides how to change scope. History is never rewritten.

## ADR-015 Licensing
- **Status:** ACCEPTED 2026-11-03
- **Decision:** Our code is MIT-licensed, as the event requires MIT-compliant submissions. Dependencies must be permissive (MIT, BSD, Apache-2.0, ISC, PostgreSQL, MPL-2.0 file-level). Containers we only run unmodified (Keycloak Apache-2.0, PostgreSQL, Caddy Apache-2.0, restic BSD-2) are fine. **No AGPL or GPL code linked into our application.** Model weights each have their own licence, which must be checked before use: some Vultr catalog models are evaluation-only, such as `auk` (TTS), which is marked evaluation only.
- **Consequences:** Task P1-02 adds a licence check (`pip-licenses`, `license-checker`) to `make check`.

## ADR-016 Monorepo and task runner
- **Status:** ACCEPTED 2026-11-03
- **Decision:** One repository holding `backend/`, `frontend/`, `deploy/`, `evals/`, `docs/` and `data/`. A root `Makefile` is the single entry point for every command (`make dev`, `make check`, `make test`, `make gen-api`, `make e2e`, `make eval`, `make images`). CI, once authorised, only calls `make` targets.
- **Why:** Lower-end models do best with one obvious command per job.

## ADR-017 Observability without third-party telemetry by default
- **Status:** ACCEPTED 2026-11-03
- **Decision:** JSON logs with `request_id`, `business_id` and `user_id` (no message bodies or prompts). Internal Prometheus metrics endpoint. `/healthz` and `/readyz`. An admin "System" page shows job queue age, failed jobs, backup age and certificate expiry. Error reporting to Sentry or GlitchTip is off by default and opt-in per customer.
- **Why:** Privacy promise to customers; low operations overhead.

## ADR-018 Regulated data profile (health clinics later, by configuration)
- **Status:** ACCEPTED 2026-11-03; §1 amended 2026-11-05 by ADR-020 (`regulated` = local models only) (owner, in chat: "make our AI ERP system general that can be used with health clinics like dental in future just by providing a BAA model API key")
- **Context:** v1 and the event exclude healthcare ([01](01-product-and-scope.md), Q10 in [13](13-risks-and-open-questions.md)). Public terms checked on 2026-11-03: Vultr offers HIPAA-configurable virtual servers only after a review and does not name a BAA; Vultr Serverless Inference makes no HIPAA statement; OpenAI, Anthropic and Google (Vertex AI) state that they sign BAAs. A clinic edition must therefore be able to use a different model provider, and possibly a different host and backup target, **without code changes**.
- **Decision:** one install-wide setting, `DATA_PROFILE=standard | regulated` (default `standard`). Everything that differs is configuration:
  1. **AI:** each provider in `ai-routes.yaml` gets `baa: true | false` (default false), which the operator sets only after signing that provider's BAA. A new routing policy `regulated` allows only providers with `baa: true` and local models (`hosted: false`). In the regulated profile it is forced for every business and cannot be overridden in settings. If no allowed route exists, AI features are disabled and the UI says so; nothing falls back to a non-BAA provider (fail closed).
  2. **Hosting and backups:** the operator attests in `.env` that the host and the backup target are covered (`REGULATED_HOST_BAA=<provider, date>`, `REGULATED_BACKUP_BAA=<provider, date>`). With the regulated profile the API refuses to start without both, and the System page shows them. The backup target stays any S3-compatible store (restic), so a BAA-covered bucket needs no code.
  3. **Application behaviour:** the profile turns on read-access audit for customer records (who viewed whom), a retention hold that turns erasure into "restrict and redact contact details" while records must be kept, minimal message templates (reminders without service names or notes), a stricter public-chat notice ("do not send medical details here"), and shorter session lifetimes.
  4. **Words:** per-business terminology overrides (for example Customer → Patient, Technician → Provider, Service → Treatment) are available in every profile, through locale overrides, not code.
- **Not decided here:** clinical features (charts, treatment plans, imaging, insurance claims). They are a separate product decision after a legal review.
- **Honest limit:** the profile makes the software ready; it does not make an installation compliant. Compliance needs the signed BAAs, the clinic's own policies and a legal review, and the README and UI must say so.
- **Consequences:** P5-01 implements the `baa` flag and the `regulated` policy (after kickoff, EVENT). The non-AI parts are task P8-23 (PRE, may be built before kickoff). No healthcare claims in the hackathon submission.

### As built (2026-11-03) — P8-23, non-AI half

Implemented the install profile and startup attestations, System display, throttled
read audits, restriction with retained records, minimal appointment template variants,
public chat notice, 15-minute idle / 8-hour staff sessions, and per-business UI terminology.
See the [regulated profile runbook](../runbooks/regulated-profile.md) for switching,
coverage and honest limits. Restriction runs the same erasure checks for open proposals (including linked records)
and upcoming held/confirmed appointments; it does not invoke destructive erasure handlers. Restrictions cannot be
lifted in this version. Existing Audit action filters expose read activity. No automatic
reminder caller exists yet. AI remains unbuilt/off until P5-01 after kickoff.

### As built (2026-11-06) — P10-E0 routing, ADR-020 decision 5

§1's original BAA allowance is superseded: regulated requires `hosted: false` and `vendor: local`,
plus private-address resolution at router build, for every class/fallback. The legacy `baa` key loads
with `ai_routes_baa_ignored` warnings under regulated; it grants no access. Failed/public resolution
fails closed. Effective policy is re-read at each provider attempt, after budget waits and before
accepting a result. `/ai/status` reports availability/reasons for all classes. The local example and
network verification procedure are in the runbook. No local model benchmark or deployment in this slice.

## ADR-019 Test SMTP server for P8-02
- **Status:** ACCEPTED 2026-11-03 (owner, in chat, left the choice between aiosmtpd and Mailpit to the agent)
- **Decision:** `aiosmtpd` (Apache-2.0, with `atpublic` and `attrs`) as a backend **dev-only** dependency. Tests start an in-process SMTP server; no container is added to the dev stack. Mailpit was the alternative (a mail-catcher container with a web view); not chosen, to keep the stack unchanged.
- **Decision (owner, 2026-11-03, in chat):** `pyyaml` (MIT) is a direct backend dependency, for the AI route file loader. It was already installed through `uvicorn[standard]`; listing it makes the dependency explicit. Same locked version (6.0.3).
- **Not decided here:** the production sending provider (Q7). Each customer configures `SMTP_URL`; until then e-mail stays simulated.

## ADR-020 AI workflows: autonomy rules, AI team, local-only models for regulated data
- **Status:** ACCEPTED 2026-11-05 (owner, in chat: "Approve as proposed" for [P10](phases/P10-ai-workflows.md)).
- **Decision 1 (autonomy):** AI proposals can be decided by a business's autonomy rule as well as by a person. A rule's
  decision is a *policy* decision delegated by the owner/admin who granted the rule; execution re-checks the grantor's
  permissions, the rule and the kill switch. Levels `off`, `draft`, `ask`, `act_notify`, `act`; every rule starts in
  shadow mode; starting defaults are the table in P10 §3. Rules never govern a person's manual proposals.
- **Decision 2 (confirm in chat):** for assistant-requested actions, the requester's **Confirm** on the action card is
  their approval when they hold the approve permission and the action is not second-person. Limit for purchase orders
  confirmed this way: $500 per order, $1,000 per day (business currency, incl. tax and shipping).
- **Decision 3 (team):** agents are configurations over the existing events and jobs (P10 §4), with correlation ids,
  a fixed transition graph and workflow-wide budgets; no generic second task queue.
- **Decision 4 (knowledge base):** v1 is owner-written text articles with full-text search and citations; embeddings
  and PDF upload later (C2).
- **Decision 5 (amends ADR-018 §1):** the `regulated` routing policy allows **local routes only** (`hosted: false`),
  for every model class and fallback, including STT, polishing and embeddings. BAA-covered hosted providers are no
  longer allowed under `regulated`. No allowed route → AI off (fail closed).
- **Open:** D5 (how the dental example runs on the public demo) and D7 (voice length limit), asked when those steps
  start.

### P10-C2a implementation record — D4 / D9 (2026-11-06)

Owner-set decisions (not reopened): extend D4 with hybrid public retrieval and PDF-to-draft import. D9 uses
already-locked `pypdf` (BSD-3, pure Python), no additional dependency and no Python pgvector package; a small
SQLAlchemy type maps `vector(1024)`. Vultr `qwen3-embedding-4b` uses explicit dimensions 1024 (owner's live
verification; not rerun by this task). Input-token price 0.05 USD/million is an unconfirmed placeholder the operator must verify.
Uploaded PDF bytes are discarded; explicit existing publication remains the approval step. Re-ranking is deferred.
The migration uses `CREATE EXTENSION IF NOT EXISTS vector`; observed `smb_owner` is not superuser and prepared
`smb_c2a` already has vector 0.8.7. Fresh installations require an administrator to preinstall the untrusted
extension; this task changes no database roles. Detailed conditions/evidence: [C2a card](ai/cards/P10-C2a.md).

Owner/reviewer follow-up (2026-11-06): production Linux must set PDF `RLIMIT_AS` or fail closed. Native macOS
development skips AS only, emitting one structured warning while retaining CPU/wall/file/core/character limits;
no memory watchdog. The reviewer verifies actual Linux enforcement in the API image. The production init script
already installs vector, so `CREATE EXTENSION IF NOT EXISTS vector` stays. The reviewer reports freeing the shared
3.9 GB tmpfs test server; agents run focused C2a checks only, with full backend/aggregate gates serialized at merge.
