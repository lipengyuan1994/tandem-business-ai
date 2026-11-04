# Task status

Update this table when you start or finish a task ([12-agent-playbook.md](../12-agent-playbook.md#the-task-loop)). Status values: `PLANNED`, `IN PROGRESS — <agent> <UTC date>`, `DONE`, `VERIFIED`, `BLOCKED — <reason>`. Kinds: see [README](README.md#eligibility-split).

| Task | Title | Kind | Tier / owner | Depends on | Status |
|---|---|---|---|---|---|
| [P0-01](P0-foundation.md) | Review the roadmap and answer open questions | GATE | Owner | — | DONE (2026-11-03, owner approved) |
| [P0-02](P0-foundation.md) | Ask the organisers about the pre-built non-AI base | GATE | Owner | — | DONE (2026-11-03, owner confirms lablab allows local pre-building) |
| [P0-03](P0-foundation.md) | Approve the local toolchain | GATE | Owner | — | DONE (2026-11-03) |
| [P0-04](P0-foundation.md) | Adopt the new AGENTS.md | GATE | Owner | P0-01 | DONE (2026-11-03) |
| [P0-05](P0-foundation.md) | Commit policy and first commit | GATE | Owner | P0-04 | DONE (2026-11-03: decision = no commits) |
| [P0-06](P0-foundation.md) | Synthetic dataset matches the roadmap | DOC | B | — | DONE (2026-11-03) |
| [P1-01](P1-platform-kernel.md) | Repository skeleton and toolchain | PRE | B | P0-03, P0-04 | DONE (2026-11-03) |
| [P1-02](P1-platform-kernel.md) | Development Compose stack | PRE | A | P1-01 | DONE (2026-11-03) |
| [P1-03](P1-platform-kernel.md) | Platform basics: config, logging, errors, ids, clock, money, canonical, pagination | PRE | B | P1-01 | DONE (2026-11-03) |
| [P1-04](P1-platform-kernel.md) | Database layer, models base, row-level security helpers, first migrations | PRE | A | P1-02, P1-03 | DONE (2026-11-03) |
| [P1-05](P1-platform-kernel.md) | Context, permissions, registry, app factory, health | PRE | A | P1-04 | DONE (2026-11-03) |
| [P1-06](P1-platform-kernel.md) | Audit writer | PRE | A | P1-05 | DONE (2026-11-03) |
| [P1-07](P1-platform-kernel.md) | Jobs, worker loop, cron, reaper | PRE | A | P1-05 | DONE (2026-11-03) |
| [P1-08](P1-platform-kernel.md) | Domain events | PRE | A | P1-07 | DONE (2026-11-03) |
| [P1-09](P1-platform-kernel.md) | Outbound HTTP, idempotency, rate limiting | PRE | A | P1-05 | DONE (2026-11-03) |
| [P1-10](P1-platform-kernel.md) | Architecture tests | PRE | A | P1-05 | DONE (2026-11-03) |
| [P1-11](P1-platform-kernel.md) | Test toolkit | PRE | B | P1-05 | DONE (2026-11-03) |
| [P1-12](P1-platform-kernel.md) | Frontend shell and API client pipeline | PRE | B | P1-01, P1-05 | DONE (2026-11-03) |
| [P1-13](P1-platform-kernel.md) | `make check` and local CI | PRE | B | P1-10, P1-12 | DONE (2026-11-03) |
| [P2-01](P2-core-services.md) | org module | PRE | B | P1-13 | DONE (2026-11-03) |
| [P2-02](P2-core-services.md) | identity: tables, roles, sessions | PRE | A | P2-01 | DONE (2026-11-03) |
| [P2-03](P2-core-services.md) | identity: Keycloak OIDC login (backend-for-frontend) | PRE | A | P2-02 | DONE (2026-11-03) |
| [P2-04](P2-core-services.md) | identity: staff, invitations, bootstrap CLI | PRE | A | P2-03 | DONE (2026-11-03) |
| [P2-05](P2-core-services.md) | audit module | PRE | B | P2-02 | DONE (2026-11-03) |
| [P2-06](P2-core-services.md) | approvals engine | PRE | A | P2-02, P1-07, P1-08 | DONE (2026-11-03) |
| [P2-07](P2-core-services.md) | notifications: outbox and in-app | PRE | A | P2-06 | DONE (2026-11-03) |
| [P2-08](P2-core-services.md) | Frontend: authentication and settings | PRE | B | P2-03, P2-04, P2-01, P1-12 | DONE (2026-11-03) |
| [P2-09](P2-core-services.md) | Frontend: approvals, audit, notifications | PRE | B | P2-06, P2-05, P2-07 | DONE (2026-11-03) |
| [P2-10](P2-core-services.md) | Integration test: proposal lifecycle through the API | PRE | A | P2-06, P2-07 | DONE (2026-11-03) |
| [P3-01](P3-business-modules.md) | catalog backend | PRE | B | P2-06 | DONE (2026-11-03) |
| [P3-02](P3-business-modules.md) | crm backend | PRE | B | P2-06 | DONE (2026-11-03) |
| [P3-03](P3-business-modules.md) | inventory backend | PRE | A | P3-01 | DONE (2026-11-03) |
| [P3-04](P3-business-modules.md) | scheduling: resources, availability and slot finder | PRE | A | P2-01, P3-01 | DONE (2026-11-03) |
| [P3-05](P3-business-modules.md) | scheduling: holds, bookings and executors | PRE | A | P3-04, P3-03, P3-02, P3-06 | DONE (2026-11-03; `POST /scheduling/holds` added) |
| [P3-06](P3-business-modules.md) | conversations backend (including public chat) | PRE | B (visitor token and rate limits reviewed by Tier A) | P3-02, P2-07 | DONE (2026-11-03; appointment requests moved to scheduling) |
| [P3-07](P3-business-modules.md) | dashboard backend | PRE | B | P3-05, P3-06, P3-03 | DONE (2026-11-03; 21 ms median on the seed) |
| [P3-08](P3-business-modules.md) | crm executors | PRE | B | P3-02 | DONE (2026-11-03) |
| [P3-09](P3-business-modules.md) | Frontend: customers | PRE | B | P3-02, P2-08 | DONE (2026-11-03) |
| [P3-10](P3-business-modules.md) | Frontend: catalog and inventory | PRE | B | P3-01, P3-03 | DONE (2026-11-03) |
| [P3-11](P3-business-modules.md) | Frontend: schedule | PRE | B (calendar component reviewed by Tier A) | P3-05 | DONE (2026-11-03; week view and drag-to-reschedule deferred to v1) |
| [P3-12](P3-business-modules.md) | Frontend: inbox and public pages | PRE | B | P3-06 | DONE (2026-11-03; widget 74 KB gzip) |
| [P3-13](P3-business-modules.md) | Frontend: Today and action-specific approval forms | PRE | B | P3-07, P3-05, P2-09 | DONE (2026-11-03; inline approve on Today not built: cards link to the proposal) |
| [P3-14](P3-business-modules.md) | Seed command | PRE | B | P3-05, P3-06, P3-08 | DONE (2026-11-03; `--reset` also clears demo slugs bootstrapped by hand) |
| [P3-15](P3-business-modules.md) | Integration and concurrency suite | PRE | A | P3-05, P3-06 | DONE (2026-11-03) |
| [P3-16](P3-business-modules.md) | End-to-end tests (Playwright) | PRE | B | P3-09 … P3-14 | DONE (2026-11-03; flagship proposal created via API until P5) |
| [P4-01](P4-packaging-and-deployment.md) | Production images | PRE | A | P1-13 | DONE (2026-11-03; sizes above the targets, see 10 as-built) |
| [P4-02](P4-packaging-and-deployment.md) | Production Compose, Caddy, Postgres init, Keycloak production config | PRE | A | P4-01 | DONE (2026-11-03) |
| [P4-03](P4-packaging-and-deployment.md) | Backup and restore | PRE | A | P4-01 | DONE (2026-11-03; `make backup-test` passes) |
| [P4-04](P4-packaging-and-deployment.md) | Terraform module and demo environment | PRE | A | — | DONE (2026-11-03; validate only, no plan or apply) |
| [P4-05](P4-packaging-and-deployment.md) | cloud-init template | PRE | A | P4-04 | DONE (2026-11-03; checked with real `cloud-init schema` in ubuntu:24.04) |
| [P4-06](P4-packaging-and-deployment.md) | install, upgrade and smoke scripts | PRE | A | P4-02, P4-03, P4-05 | DONE (2026-11-03; install.sh verified by dry run only) |
| [P4-07](P4-packaging-and-deployment.md) | Local production rehearsal | PRE | B | P4-06 | DONE (2026-11-03; `make rehearse` passes in 181 s) |
| [P4-08](P4-packaging-and-deployment.md) | Runbooks | PRE | B | P4-06 | DONE (2026-11-03; all runbooks cold-read twice by Codex and revised) |
| [P4-09](P4-packaging-and-deployment.md) | First Vultr deployment (demo) | **DEPLOY** | A | P4-07, P0-02, kickoff credits | PLANNED |
| [P5-01](P5-ai-layer.md) | Inference gateway, router and catalog probe | EVENT | A | P4-09 (key), P3 gate | PLANNED |
| [P5-02](P5-ai-layer.md) | Local CPU model benchmark (strict-private feasibility) | EVENT + DEPLOY | A | P5-03 (eval harness) | PLANNED |
| [P5-03](P5-ai-layer.md) | Eval harness, cases and model selection | EVENT | A (harness) / B (cases) | P5-01 | PLANNED |
| [P5-04](P5-ai-layer.md) | Agent protocol, runner, recording and budgets | EVENT | A | P5-01 | PLANNED |
| [P5-05](P5-ai-layer.md) | Agent tools | EVENT | A | P5-04 | PLANNED |
| [P5-06](P5-ai-layer.md) | Intake use case | EVENT | A | P5-05 | PLANNED |
| [P5-07](P5-ai-layer.md) | Copilot chat (SSE) | EVENT | A | P5-05 | PLANNED |
| [P5-08](P5-ai-layer.md) | Summary, suggested reply, reorder note, briefing | EVENT | B (Tier A review) | P5-04 | PLANNED |
| [P5-09](P5-ai-layer.md) | Frontend AI features | EVENT | B | P5-06, P5-07, P5-08 | PLANNED |
| [P5-10](P5-ai-layer.md) | Safety pass | EVENT | A | P5-06 | PLANNED |
| — | **AI build kit (2026-11-03, owner-approved DOC work):** P5 tasks are built from the cards in [../ai/](../ai/README.md). Track the cards here. | — | — | — | — |
| [PR-00](../ai/cards/PR-00-parallel-workspaces.md) | Parallel agent workspaces | PRE | B | — | DONE (2026-11-03; Codex GPT-6.1 Sol, verified: two full suites ran concurrently on separate databases) |
| [PR-01](../ai/cards/PR-01-tool-read-functions.md) | Read functions and executors the AI tools need | PRE | B | — | DONE (2026-11-03; Codex Sol, verified; flaky tests fixed) |
| [PR-02](../ai/cards/PR-02-platform-prerequisites.md) | Platform prerequisites for safe AI orchestration | PRE | A | — | DONE (2026-11-03; Codex Sol, Sol review: 3 findings fixed incl. DNS rebinding and stale job attempts; verified) |
| [PQ-01](../ai/cards/PQ-01-procurement-groundwork-backend.md) | Procurement groundwork backend | PRE | A | PR-02 | DONE (2026-11-03; Codex Sol, Sol review: 4 findings fixed incl. quote tokens in logs; verified) |
| [PQ-02](../ai/cards/PQ-02-procurement-groundwork-frontend.md) | Procurement groundwork screens | PRE | B | PQ-01 | DONE (2026-11-03; Codex Sol, verified: procurement e2e passes; widget 77.6 KB gzip) |
| [AI-01](../ai/cards/AI-01-module-skeleton.md) | ai module skeleton and tool framework | EVENT | A | kickoff, PR-02 | DONE (2026-11-03; built by a Claude Opus 5.5 agent, reviewed here: effect key now includes the trigger) |
| [AI-02](../ai/cards/AI-02-gateway-adapters.md) | Gateway adapters and FakeGateway | EVENT | A | AI-01 | DONE (2026-11-03; Claude Opus 5.5 track 1) |
| [AI-03](../ai/cards/AI-03-router-and-policy.md) | Router, route file, policies | EVENT | A | AI-02 | DONE (2026-11-03; Claude Opus 5.5 track 1; rehearse not re-run yet) |
| [AI-04](../ai/cards/AI-04-probe.md) | Live catalog probe | EVENT | B | AI-03, key | DONE (2026-11-03; probe built by Codex GPT-6.1 Sol, run live here: 9 Vultr text models answer the JSON protocol, 6 pass the tool-call check; `backend/evals/reports/2026-11-03-probe.md`; first route file `deploy/config/ai-routes.yaml`) |
| [AI-05](../ai/cards/AI-05-runner.md) | Runner, structured completion, budgets | EVENT | A | AI-03 | DONE (2026-11-03; Claude Opus 5.5 track 1) |
| [AI-06](../ai/cards/AI-06-read-tools.md) | Read tools | EVENT | A | AI-01 | DONE (2026-11-03; Claude Opus 5.5 track 2) |
| [AI-07](../ai/cards/AI-07-propose-tools.md) | Propose and hold tools | EVENT | A | AI-06 | DONE (2026-11-03; Claude Opus 5.5 track 2; payment-reminder permission question open) |
| [AI-08](../ai/cards/AI-08-intake.md) | U1 Intake part 1 (trigger, safety, matching, guards) | EVENT | A | AI-05, PR-02 | DONE (2026-11-03; Codex GPT-6.1 Sol, reviewed here) |
| [AI-08B](../ai/cards/AI-08B-intake-agent.md) | U1 Intake part 2 (agent run, prompt) | EVENT | A | AI-08, AI-07 | DONE (2026-11-03; Codex GPT-6.1 Sol, reviewed here: evidence fix, prompt wording) |
| [AI-09](../ai/cards/AI-09-evals.md) | Evals and model selection | EVENT | A/B | AI-05 (+ use cases) | PARTIAL (2026-11-03; harness + 130 cases; first live run: injection resistance 100 % in every suite; summary and copilot meet thresholds; intake, time zones and procurement do not yet — being fixed) |
| [AI-10](../ai/cards/AI-10-copilot.md) | U2 Copilot (SSE) | EVENT | A | AI-05, AI-07 | DONE (2026-11-03; Codex GPT-6.1 Sol, reviewed here: thread models renamed `CopilotThreadOut`/`CopilotThreadDetailOut`) |
| [AI-11](../ai/cards/AI-11-single-shot-writers.md) | U3 summary, U4 reorder note, U6 briefing | EVENT | B | AI-05, AI-07, PR-01 | DONE (2026-11-03; Codex GPT-6.1 Sol, reviewed here; `approvals.find_open_proposals(item_id=)` added for the reorder guard) |
| [AI-12](../ai/cards/AI-12-procurement-agent.md) | U9 Procurement agent | EVENT | A | AI-05, AI-07, AI-11, PQ-01 | DONE (2026-11-03; Codex GPT-6.1 Sol, reviewed here; `purchasing.find_open_quote_request` added) |
| [AI-13](../ai/cards/AI-13-frontend.md) | Frontend AI features | EVENT | B | AI-01 (mocks) | DONE (2026-11-03; Claude Sonnet 5.5 built against mocks, Codex GPT-6.1 Sol wired to the generated API; 30 e2e incl. the AI panel pass) |
| [AI-14](../ai/cards/AI-14-safety-pass.md) | Safety pass | EVENT | A | AI-08B | PARTIAL (2026-11-03; Codex GPT-6.1 Sol: offline safety pass, 53 tests, 4 findings fixed incl. 1 high, `docs/security/2026-11-03-ai-review.md`; live injection run waits for the key) |
| [AI-15](../ai/cards/AI-15-campaign-draft-and-payment-followup.md) | U5 campaign draft, U10 payment follow-up (optional) | EVENT | B | AI-11, PR-01 | DONE (2026-11-03; Codex GPT-6.1 Sol, reviewed here; owner decision: `sales.document.write` only for the background payment follow-up) |
| [P6-01](P6-event-hardening.md) | Marketing backend | PRE-type work (non-AI); may start before kickoff if P3 finished early | B | P3-02, P2-07 | DONE (2026-11-03; all M12 tests pass) |
| [P6-02](P6-event-hardening.md) | Marketing frontend and AI draft | EVENT (AI draft) | B | P6-01, P5-08 | PARTIAL (2026-11-03): non-AI UI built (segment builder, preview, revisions, submit); "Draft with AI" waits for kickoff |
| [P6-03](P6-event-hardening.md) | Voice (stretch; first item on the cut list) | EVENT | A | P5-06 | PLANNED |
| [P6-04](P6-event-hardening.md) | Demo data and demo script | DOC + PRE | B | P5-06 | PARTIAL (2026-11-03): `reset-demo` command and `docs/demo-script.md` (steps that need AI marked). 2026-11-03: procurement and payment-reminder demo data and script segment; the two rehearsals on the demo URL are not done |
| [P6-05](P6-event-hardening.md) | Deploy and verify on Vultr | DEPLOY | A | P5 gate | PLANNED |
| [P6-06](P6-event-hardening.md) | Security pass | PRE/EVENT | A | P6-05 | PARTIAL (2026-11-03): local half done, see `docs/security/2026-11-local-review.md`; local re-scan 2026-11-03 (`docs/security/2026-11-03-rescan.md`: no high or critical; all findings fixed the same day except accepted items: Caddy CVEs, `Via` header); scan of the public demo URL open |
| [P6-07](P6-event-hardening.md) | Performance sanity | PRE | B | P6-05 | PARTIAL (2026-11-03): local run passes (p95 26 ms, 0 errors, `docs/performance-local.md`); the demo VM run is open |
| [P6-08](P6-event-hardening.md) | Restore drill on the demo | DEPLOY | A | P6-05 | PLANNED |
| [P6-09](P6-event-hardening.md) | Minimal System page | PRE | B | P4-03 | DONE (2026-11-03) |
| — | **Owner decision 2026-11-03 (chat):** "Follow your proposed order": P8-04 sales, then P8-05 purchasing, then P8-02 SMTP are built before kickoff as PRE work (non-AI), with coding delegated to GPT-6.1 Sol/Terra via Codex and verified here. P8 rows below track them. | — | — | — | — |
| [P8-04](P8-production-v1.md) | sales module: quotes, orders, invoices, numbering | PRE (moved before kickoff) | A/B | P3 | DONE (2026-11-03; built by Codex GPT-6.1 Sol, reviewed by a second Sol run, verified here: 295 backend, 147 frontend, 17 e2e tests) |
| [P8-05](P8-production-v1.md) | purchasing: suppliers, POs, receipts, reorder draft conversion | PRE (moved before kickoff) | B | P8-04 | DONE (2026-11-03; built by Codex GPT-6.1 Sol, verified here; Sol review: 2 findings fixed) |
| [P8-23](P8-production-v1.md) | Regulated data profile, non-AI half (ADR-018) | PRE (may run before kickoff) | A | — | DONE (2026-11-03; built by Codex GPT-6.1 Sol, Sol review: 5 findings fixed, verified here; AI half is P5-01) |
| [P8-02](P8-production-v1.md) | SMTP adapter, templates, Keycloak SMTP | PRE (moved before kickoff) | A | — | DONE (2026-11-03; built by Codex GPT-6.1 Sol, verified here; Sol review: 3 findings fixed; Keycloak mail is a documented manual step; no real provider tested) |
| [P7-01](P7-submission.md) | Publish the repository | GATE | Owner | P6 gate | PLANNED |
| [P7-02](P7-submission.md) | Judge-facing README | DOC | B | P6-04 | PARTIAL (2026-11-03): draft `docs/submission/README-judges.draft.md`, cold-read by a second model; updated 2026-11-03 for sales, purchasing and procurement; AI facts after kickoff |
| [P7-03](P7-submission.md) | Demo video | GATE | Owner | P6-04 | PLANNED |
| [P7-04](P7-submission.md) | Slides | DOC | B draft, owner final | — | PARTIAL (2026-11-03): outline `docs/submission/slides-outline.draft.md`; procurement added 2026-11-03 |
| [P7-05](P7-submission.md) | Submit on lablab.ai | GATE | Owner | P7-01 … P7-04 | PLANNED |
| [P7-06](P7-submission.md) | Freeze | PRE | B | — | PLANNED |
