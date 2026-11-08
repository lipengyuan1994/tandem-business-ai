# Event work record (after kickoff)

Kickoff of the Vultr "Agent Rush" event: **2026-11-03 15:00 UTC**. Submission deadline: 2026-11-08 22:00 UTC.
Work done before kickoff is recorded in [`PRE_EVENT_WORK.md`](PRE_EVENT_WORK.md); all AI code (inference calls,
prompts, agent loops, AI tools, evals) is written after kickoff and recorded here with UTC times. Rows are appended,
never rewritten.

## Log

| UTC time | What | Kind | Files |
|---|---|---|---|
| 2026-11-03 ~17:45 | Kickoff check (17:45 UTC, after 15:00). Credit claim steps looked up on the event page; the owner claims the $200 Vultr credit (needs the owner's lablab sign-in and a new Vultr account) | EVENT | — |
| 2026-11-03 ~17:50–18:30 | **AI-01 `ai` module skeleton** (built by a Claude Opus 5.5 agent, reviewed here): 6 tables with forced row-level security (migration `20261103_1600_ai_core`), `GET /api/v1/ai/status`, permissions, export and erasure, tool framework (registry, `execute_tool`, idempotent effects) tested with dummy tools. Review change: the effect key also includes the trigger, so a new message cannot get an earlier, closed proposal back (contracts §4 updated). 759 backend tests, `make check` passed | EVENT | `backend/app/modules/ai/`, `backend/migrations/versions/20261103_1600_ai_core.py`, `backend/app/platform/config.py`, `docs/roadmap/ai/{contracts,HANDOFF}.md` |
| 2026-11-03 ~18:30 | Three parallel tracks started: track 1 gateway, router and runner (AI-02, AI-03, AI-05); track 2 read and propose tools (AI-06, AI-07); track 3 frontend AI screens against mocks (AI-13) | EVENT | — |
| 2026-11-03 ~18:30–19:35 | **Track 1 — AI-02 gateway, AI-03 router and policy, AI-05 runner** (Claude Opus 5.5 agent): OpenAI-compatible, Anthropic and scripted fake adapters with error mapping and no content in logs; YAML route file with policies (incl. `regulated`), fallback and pricing; dev route file using the fake provider (refused in production); production compose/env/install/upgrade/smoke wiring; agent protocol, runner with budgets and step limits, structured single-shot completion with one repair, run/step/usage recording, `GET /ai/runs` and `/ai/usage`. 896 backend tests, `make check` passed | EVENT | `backend/app/modules/ai/{gateway,agent}/`, `ai/{recording,usage,structured,api,public}.py`, `deploy/config/`, `deploy/compose/`, `deploy/scripts/` |
| 2026-11-03 ~18:30–19:30 | **Track 2 — AI-06 read tools, AI-07 propose and hold tools** (Claude Opus 5.5 agent; paused once by a usage limit): 16 read tools (no customer contact values; free text redacted), `hold_slot`, 9 propose tools (titles from looked-up data, evidence attached, plain-text model text only, quantities from inventory, idempotent through `ai_effects`). Open: the payment-reminder executor needs `sales.document.write`, which assistant contexts lack | EVENT | `backend/app/modules/ai/tools/`, `ai/tests/test_{read,propose}_tools.py` |
| 2026-11-03 ~18:30–18:55 | **Track 3 — AI-13 frontend** (Claude Sonnet 5.5 agent) against mocked APIs: assistant panel with streaming steps (Ctrl/Cmd+J), Summarise in Inbox, AI badge and evidence on proposals, briefing card on Today, paste-supplier-reply dialog, "Ask AI to request quotes", AI settings page with usage and run history. 270 frontend tests; initial JS 210 KiB gzip | EVENT | `frontend/src/features/ai/`, small additions to existing pages, `frontend/e2e/ai.e2e.ts` |
| 2026-11-03 ~18:50 | Owner in chat: coding from now on goes to the Codex CLI (GPT-6.1 Sol for code, Terra for prose) to save Claude usage; Claude writes briefs, reviews and verifies. Dev `.env` switched to the fake AI route file | — | `.env` (local, not committed) |
| 2026-11-03 ~18:55–19:30 | **AI-08 intake part 1** (Codex GPT-6.1 Sol, reviewed here): subscriber on inbound visitor/customer messages → non-transactional job `ai.intake`; open-proposal guard; safety screen (prompt `safety.v1`, fails open as specified); deterministic customer matching; one outcome note per message; agent step is a placeholder until AI-08B. 43 intake/safety tests | EVENT | `backend/app/modules/ai/{subscribers,jobs}.py`, `ai/usecases/`, `ai/prompts/safety.v1.md` |
| 2026-11-03 ~19:00–19:45 | **AI-08B intake agent** (Codex GPT-6.1 Sol, reviewed here): real intake run replacing the placeholder — task with business-local time and no contact values, trusted business brief and opening hours, the inbound message fenced as untrusted, visitor details only in server facts; prompt `intake.v1` (hold then book for existing customers, no hold for new ones, one clear question when unclear, never promise prices, at most one proposal); dev fake-script walk-through. Review fixes: booking evidence now cites stock checks on the service's parts; prompt keeps "slot not held" out of the customer reply. 47 intake tests | EVENT | `ai/usecases/intake.py`, `ai/prompts/intake.v1.md`, `ai/tools/proposals.py`, `deploy/config/ai-fake-script.dev.yaml` |
| 2026-11-03 ~19:00–20:00 | **AI-11 summary, reorder note, briefing** (Codex GPT-6.1 Sol, reviewed here): `POST /ai/conversations/{id}/summary` (summary, open questions, suggested reply as a reply proposal through the `propose_reply` tool path), reorder note on low stock (quantities from inventory, note on the proposal, skipped when a reorder for the item is pending — new `approvals.find_open_proposals(item_id=)`), `GET /ai/briefing` per user per hour; code check that every number in the output appears in the facts (one repair). Review fix: ordinal words no longer count as numbers | EVENT | `ai/usecases/{summary,reorder_note,briefing}.py`, `ai/prompts/`, `approvals/{service,public}.py`, `ai/tools/proposals.py` |
| 2026-11-03 ~19:25–19:50 | **AI-10 copilot chat over SSE** (Codex GPT-6.1 Sol, reviewed here): own threads only, tools limited to the user's permissions, steps/proposals/final streamed as server-sent events, disconnect stops after the current step, 30 runs per user per hour, messages kept 90 days, Caddy flushes the stream. Review fix: thread models renamed `CopilotThreadOut`/`CopilotThreadDetailOut` (name clash with the Inbox; contracts §14 updated). `make check` passed: 1,006 backend, 270 frontend tests | EVENT | `ai/usecases/copilot.py`, `ai/prompts/copilot.v1.md`, `ai/api.py`, `deploy/compose/Caddyfile{,.dev}` |
| 2026-11-03 ~20:05 | Started on Codex GPT-6.1 Sol: AI-12 procurement agent; wiring the AI screens to the generated API types | EVENT | — |
| 2026-11-03 ~20:05–20:45 | **AI-12 procurement agent** (Codex GPT-6.1 Sol, reviewed here): request quotes for a low-stock item (quantity from the reorder suggestion, suppliers chosen from the tool's list, skipped when a quote request is already open — new `purchasing.find_open_quote_request`), read a pasted supplier reply (every number must appear in the supplier text, otherwise no proposal and the field is flagged), compare quotes and propose a PO for the top-ranked or a justified alternative, chase late orders and unanswered invites at most every 3 days. Every step is a proposal needing approval. 4 prompts; dev fake-script demo path (request → supplier answers → compare → PO proposal) passes. 401 AI + purchasing tests | EVENT | `ai/usecases/procurement{,_fake}.py`, `ai/prompts/`, `ai/{api,jobs,subscribers,registration}.py`, `purchasing/{public,quote_reads}.py` |
| 2026-11-03 ~20:05–20:30 | **AI screens wired to the real API** (Codex GPT-6.1 Sol): generated types for status, runs, usage, copilot, summary and briefing; SSE stays on `fetch`; rate-limit message for `platform.rate_limited`. 271 frontend tests. `make e2e` on the dev stack: 30 passed, including the AI assistant panel against the fake provider | EVENT | `frontend/src/features/ai/` |
| 2026-11-03 ~20:20 | Started on Codex GPT-6.1 Sol: AI-09 eval harness and 130 synthetic cases (live runs wait for the key); AI-14 safety pass (offline part) | EVENT | — |
| 2026-11-03 ~20:20 – 2026-11-03 ~00:15 | **AI-09 eval harness** (Codex GPT-6.1 Sol): `backend/evals/` runner (fresh seeded business per case, frozen clock, real use cases, scoring of expected and forbidden actions, steps, latency, tokens, cost, Markdown report), `make eval` refusing without `AI_LIVE=1`, 130 synthetic cases (intake 60, injection 15, time zones 10, copilot 20, summary 10, procurement 15), 72 harness tests with the fake gateway. No live run yet (no key) | EVENT | `backend/evals/`, `Makefile`, `ai/tests/test_eval_harness.py` |
| 2026-11-03 ~20:45 – 2026-11-03 ~00:20 | **AI-14 safety pass, offline part** (Codex GPT-6.1 Sol, reviewed here): 53 tests — malicious scripted model against all 26 tools (other-business ids, disallowed tools, oversized arguments, `business_id`), only read/hold/propose write paths, budgets and rate limits, log and `ai_run_steps` canaries, regulated profile. Fixed: earlier copilot answers (which can quote customer text) replayed as trusted (high); model-chosen tool names and argument keys stored in step records; repairs not counted against the step limit; validation keys copied into repair instructions (medium). Review document `docs/security/2026-11-03-ai-review.md`; S7 accepted for the event | EVENT | `ai/agent/runner.py`, `ai/structured.py`, `ai/usecases/copilot.py`, `ai/tests/test_safety_pass_*.py`, `docs/security/` |
| 2026-11-03 ~00:30–00:59 | Supervisor verification of the combined AI layer: review fixes (copilot history test updated to the AI-14 fencing rule; eval harness test sets its test database explicitly; eval runner prints the refusal reason). `make check`: ALL PASSED — 1,165 backend, 271 frontend, deploy checks, licences, no leaks. `make e2e` (dev, fake provider): 30 passed. `make rehearse` on production images: REHEARSAL PASSED in 291 s (29 e2e; the AI e2e skips itself because AI is not configured there; failed-upgrade rollback and restore pass). Not done: any live model call (no key), deployment to Vultr | EVENT | `ai/tests/{test_copilot,test_eval_harness}.py`, `backend/evals/runner.py` |
| 2026-11-03 ~01:30–02:20 | **Live models and new owner decisions.** Owner: the AI may propose payment reminders (only the background payment follow-up gets `sales.document.write`; a person approves each); `pyyaml` is a direct dependency; first commit approved; the project continues as the owner's own product. **AI-04 probe** (Codex GPT-6.1 Sol built it, run live here): 9 Vultr text models return valid JSON, 6 also pass the agent tool-call check; first route file `deploy/config/ai-routes.yaml` (agent `glm-5.3-flash`, fast extraction `qwen3.8-flash-next`, writer `deepseek-v4.1-flash-1017`). **AI-15** campaign draft and payment follow-up (Codex GPT-6.1 Sol). **First live evals** (≈ $0.62): injection resistance 100 % in all six suites; summary 100 % and copilot 85 % pass; intake 35 %, time zones 20 %, procurement 20 % fail (intake runs hit the 8-step limit; procurement extraction errors) | EVENT | `backend/app/modules/ai/{probe.py,usecases/}`, `deploy/config/ai-routes.yaml`, `backend/evals/reports/` |
| 2026-11-03 ~02:00 | **Production push started (owner: 8 hours as tech lead, Codex does the coding, Vultr test deploys within the $200 credit).** Free private Container Registry created; spend log `docs/vultr-spend.md`. Installer gained `--demo` and `--domain auto` (sslip.io) for unattended test installs (Codex GPT-6.1 Sol, reviewed here) | EVENT | `deploy/scripts/install.sh`, `deploy/tests/test_install_demo.py`, `docs/runbooks/install.md`, `docs/vultr-spend.md` |
| 2026-11-03 ~02:30–03:30 | **First real Vultr deployment.** Images 0.1.0 (linux/amd64) pushed to the private registry; Object Storage (Standard, sjc) for backups; `install.sh --demo --domain auto` created the VM (vhp-2c-4gb-amd), firewall and SSH key, got a Let's Encrypt certificate for `45-63-85-83.sslip.io`, seeded the demo businesses, wrote Morgan's and Riley's logins to a mode-600 file on the VM, made the first backup and ran the smoke test: all checks pass except a race (backup healthcheck still "unhealthy" seconds after the first backup; passes on re-run — fix delegated). Live AI enabled on the VM (route file, key via SSH stdin, egress allowlist `api.vultrinference.com`); a live call through the production router succeeded. Spend so far $0.07 | EVENT | `docs/vultr-spend.md`, `install-smb-demo/` (local, gitignored) |
| 2026-11-03 ~02:20–03:10 | **Live-eval diagnosis:** `glm-5.3-flash` is a reasoning model that returns empty output within the 800-token cap; `json_schema` mode returns empty output for nested schemas on Vultr. Routes switched to non-reasoning models in `json_object` mode (agent and safety `mimo-v2.6-flash`, extraction `qwen3.8-flash-next`, writer `deepseek-v4.1-flash-1017`). Intake runs used 8 tool calls + final against an 8-step limit. Eval runner double-import bug fixed; eval reports now list each run's steps and error codes | EVENT | `deploy/config/ai-routes.yaml`, `backend/evals/` |
| 2026-11-03 ~03:00–04:10 | **Second-model review of the AI module** (GPT-6.1 Sol, read-only; `docs/security/2026-11-03-ai-second-review.md`): 16 findings (4 high: reorder tool disclosed stock without stock permission; procurement prices assumed 2 decimals; rejected currency fell back to the business currency; intake could create two proposals for one message). Fixed by three parallel Codex GPT-6.1 Sol batches with regression tests (procurement; intake and tools incl. intake prompt v2 with preloaded context, typical booking in 5 steps; copilot/briefing/payments/limits incl. `Idempotency-Key`, one generation per thread, input-size limits, migration `ai_operations`). AI and related module tests pass on the combined code | EVENT | `backend/app/modules/ai/**`, `approvals`, `purchasing`, `sales`, `conversations` public reads |
| 2026-11-03 ~04:15 | **Second-review batch C completion (Codex GPT-6.1 Sol; the supervising agent lifted the file restrictions once batches A and B were done).** Procurement extraction accepts persistent operation keys and resumes the same validated result/proposal; metadata-only `ai_operations.details` preserves verification flags and original currency. Shared `ModelCaller.call` trims oldest complete tool exchanges and enforces input estimates (schema/repairs included), retaining batch B deadlines and exception closing. Frontend covers all seven requested codes; Retry message reuses the original UUID and new messages get fresh UUIDs. One old trim test now rejects oversized system/task context. Final checks: AI suite run once, **648 passed in 405.68 s**; Ruff check/format (**462 files**) and mypy (**272 source files**) pass; frontend tsc/lint pass, **33 test files / 279 tests passed**. Targeted sequential backend rerun **75 passed** after a fixture collision caused by overlapping runs. No live model calls, new dependencies, commits or edits to `backend/evals/**`. | EVENT | `backend/app/modules/ai/{agent,api.py,errors.py,structured.py,usecases,tests}`, `backend/migrations/versions/20261104_0500_ai_operation_details.py`, `frontend/src/features/ai/`, `frontend/src/locales/en.json`, AI contracts/README/HANDOFF |
| 2026-11-03 ~03:50–04:15 | **0.1.1 released to the Vultr VM.** `make check`: 1,387 backend and 279 frontend tests, deploy checks, licences, no leaks. Images 0.1.1 (amd64) pushed; real `upgrade.sh 0.1.0 → 0.1.1` on the VM (new migrations applied, smoke test passed, every service healthy). **First live AI on the deployment:** a synthetic visitor chat ("brake service next Tuesday morning") → safety screen (0.6 s) → intake in 5 steps (check stock, find slots, hold, propose booking, final; 4.2 s; ≈ $0.005) → proposal "Book Brake service for Ada Example, Tue 10 Nov 11:45 MST" waiting for approval. The background jobs also ran live: a payment-reminder proposal for the overdue demo invoice and a reorder note | EVENT | `CHANGELOG.md` (0.1.1), VM `/opt/smb` |
| 2026-11-04 ~04:20–05:20 | **Production-readiness pass** (operations review by GPT-6.1 Sol, `docs/security/2026-11-03-ops-readiness-review.md`; fixes by three Codex GPT-6.1 Sol batches): worker capacity reserved for non-AI jobs, fair event delivery, 100 s drain; database pool sizes and timeouts; daily retention cleanup (migration `20261104_0700_platform_retention`); streamed exports (20 MB export under 3 MB memory); host watchdog with webhook alerts (`docs/runbooks/monitoring.md`), 7 h backup alert age and prune/check status, key-only SSH, time sync, journald limits, 120 s shutdown grace, PostgreSQL idle-transaction timeout; hard AI budget (atomic reservation, installation-wide daily/monthly USD ceilings, concurrency cap) and a per-business limit on public-chat AI runs. Fixed on the way: phone-number redaction matched digits inside UUIDs. `make check`: 1,457 backend, 279 frontend, all passed. **0.1.2 released to the VM** (`upgrade.sh 0.1.1 → 0.1.2`, compose file replaced by the runbook procedure, smoke passed; `install-host-ops.sh` run as root; watchdog 0 issues; backup maintenance ok). Found: root can log in over SSH with the install key (port 22 is limited to the admin address) | EVENT | `backend/app/platform/`, `backend/app/worker.py`, `backend/app/modules/ai/`, `deploy/**`, `docs/runbooks/` |
| 2026-11-04 ~05:20–06:40 | **Live eval tuning to thresholds** (fixes by Codex GPT-6.1 Sol, live runs here): extraction moved to `mimo-v2.6-flash` (qwen returned empty/truncated JSON on some inputs); tolerant parsing of model values with units ("USD 12.00 each", "10 pairs") while verification against the supplier text stays strict, extraction prompt v2; eval harness isolates rate-limit counters between cases; intake prompt v3 (answer stock/info questions from tool results, never choose for an undecided customer, acknowledge and hand off cancel/refund/unknown requests, clear `needs_human` meaning) and 5 case expectations aligned. **Result: every suite meets its threshold** — intake 95 % (from 25–35 % this morning), time zones 90 %, injection 100 % with 100 % resistance, copilot 80 %, summary 100 %, procurement 80 %. Selection report `backend/evals/reports/2026-11-03-selection.md`. A read-only public-endpoint load test on the VM (10 clients, 60 s): 1,030 requests, all but one connection 200, latency dominated by network and new TLS connections | EVENT | `backend/app/modules/ai/{usecases/procurement.py,prompts/}`, `backend/evals/`, `deploy/config/ai-routes.yaml` |
| 2026-11-04 ~06:10–06:40 | **0.1.3 released to the VM** (`make check`: 1,504 backend, 279 frontend, all green after two fixes — a version-pinned test and the extraction response returning an internal model type; `make e2e`: 30 passed; `upgrade.sh 0.1.2 → 0.1.3` smoke passed; live route file uploaded). Live check on the deployment: an undecided booking request ("Tuesday or Friday, still need to check") now gets a clarifying reply proposal instead of a booking (intake prompt v3, 2 steps, 1.8 s). **Found live:** a new visitor sharing only the surname with existing customers ("Bea Example") was stopped as an ambiguous customer, so her stock question got no reply — matching fix delegated (an unmatched e-mail/phone outweighs a name-only overlap; ambiguity no longer blocks informational answers) | EVENT | `CHANGELOG.md` (0.1.2, 0.1.3 sections), VM `/opt/smb` |
| 2026-11-04 ~06:40–07:40 | **0.1.4: customer matching fix**, found live (Codex GPT-6.1 Sol, refined here so "D Example" still matches Dan/Dana Example while a shared surname alone does not): an unmatched e-mail/phone outweighs name-only overlap; ambiguity no longer stops intake. Live intake suite 92 % on 62 cases (2 new shared-surname cases pass); `make check` 1,529 backend, 279 frontend; `upgrade.sh 0.1.3 → 0.1.4` smoke passed; live re-test: a new visitor sharing a surname asking about stock got a correct reply proposal ("in stock, 2 available") in 3 steps. SSH to the VM closed (only 80/443 open). Vultr spend so far $1.65 of $200 | EVENT | `backend/app/modules/{ai/usecases/intake.py,crm/public.py}`, `backend/evals/cases/intake/`, `docs/vultr-spend.md` |
| 2026-11-04 ~12:05–12:20 | **VM record brought up to date**: applied the pending in-place cloud-init `user_data` update (verified first: the provider sends one update with no reboot or reinstall, uptime unchanged afterwards); opened SSH with `access.sh`, copied the demo logins (Morgan, Riley) to the gitignored `install-smb-demo/demo-logins.txt` (mode 600, not printed), closed SSH. `terraform plan`: no changes; only 80/443 open; site ready. Prepares the signed-in capacity test, which the owner runs (their sign-in and authenticator). Vultr spend $1.96 of $200 | EVENT | `docs/vultr-spend.md`, `docs/roadmap/ai/HANDOFF.md` |
| 2026-11-04 ~18:30–18:45 | **P6-07 done: signed-in capacity test on the demo VM.** The owner signed in as Morgan (password and authenticator) with Playwright's recorder; the business was then selected through the API (the saved session had none, so the first attempt failed with 403 `auth.no_active_business`). 20 simulated staff for 5 minutes: 5,251 read requests, 0 errors, p50 113 ms, p95 204 ms, p99 487 ms, including the network round trip (target p95 < 300 ms). Session logged out and its cookie file deleted afterwards. Results in `docs/performance-local.md` | PRE | `docs/performance-local.md`, `docs/roadmap/phases/{STATUS.md,P6-event-hardening.md}` |
| 2026-11-04 ~19:10–19:45 | **Load-test script fix** (Codex GPT-6.1 Sol, reviewed here): `--business NAME_OR_ID` selects the business (session rotated in memory only, state file untouched); a session with no active business or an expired one now exits with a clear message instead of a traceback; discovery errors name the endpoint, status and problem code; TLS certificates are verified except for localhost hosts or `--insecure`. 12 offline unit tests (run under the project venv from `make deploy-check`); live check against the VM: a fake session gets the clear "not signed in or has expired" message over a verified certificate. First real use by the owner (~20:30 UTC, `--business "Example Cycle Works"`): 5,279 requests, 0 errors, p95 200 ms, pass | PRE | `deploy/scripts/loadtest.py`, `deploy/tests/test_loadtest.py`, `docs/performance-local.md` |
| 2026-11-04 ~21:20–23:45 | **Hardening (owner: "do the hardening").** P6-06 security pass on the public URL (`docs/security/2026-11-event-review.md`): ZAP baseline 0 fail / 4 warn / 64 pass on two pages; all 65,535 TCP ports scanned over IPv4 and IPv6, only 80/443 open; TLS 1.2/1.3 grade A; `pip-audit` (117 packages) and `pnpm audit` clean; headers and cookies checked live; Keycloak admin and master realm 404; app-level egress refuses everything off the allowlist, raw container egress still open (P8-09, needs an owner decision). P6-08 restore drill: fresh manual backup restored from Object Storage into scratch databases, counts identical (smb 4,393 rows, keycloak 1,627), 5 s; found that the drill's details file is missing when counts differ (fix delegated). P6-02 done: "Draft with AI" button on the campaign page (Codex GPT-6.1 Sol, reviewed here; 297 frontend tests pass). Status board: stale P5 rows marked done via their AI cards (P5-02 stays planned). Email: recommendation recorded under Q7 (simulated until the owner's domain exists, then an SMTP relay). Vultr spend $2.65 of $200 | EVENT/PRE | `docs/security/`, `docs/roadmap/phases/{STATUS.md,P6-event-hardening.md}`, `frontend/src/features/marketing/`, `docs/vultr-spend.md` |
| 2026-11-05 ~01:30–01:50 | **0.1.5 released to the VM** ("Draft with AI" on the campaign page; restore drill details for every database). Images built for amd64 and pushed (the web image checked for the new button, the backup image for the restore fix); every staged deployment file compared with the VM by checksum (only `restore.sh` changed, copied); manual backup, then `upgrade.sh 0.1.4 → 0.1.5` in 28 s, smoke passed, all services healthy; restore drill on the VM wrote details for both databases, including the differing case; SSH closed, Terraform no changes. The button's live AI run waits for a signed-in check by the owner | EVENT | `CHANGELOG.md` (0.1.5), VM `/opt/smb` |
| 2026-11-05 | **P10-A0 backend (Codex):** contract first; business autonomy rules/shadow/caps, delegated policy approvals, current-grantor execution/revocation and kill switch; open-draft intent and stale-stock guards; rule/activity APIs and working draft result link; owner-granted shadow rules in demo seed. Migration `20261105_0800_autonomy`. Requested suite **831 passed**; focused **112 passed**; additional audit/seed/architecture/migration checks **42 passed**, one stale OpenAPI snapshot fixed by backend-only regeneration, **2 contract checks passed** afterward. Ruff/format/mypy clean. Empty migration down/up and populated legacy human-decision upgrade passed. Independent review findings fixed. `make check` omitted because Docker/frontend are excluded; no frontend, external API, deployment, dependencies or commits. Full evidence and limitations in the backend report | EVENT | `backend/app/modules/{approvals,ai,inventory,org}/`, `backend/app/platform/{context,audit}.py`, migration, seed/fixtures, `backend/openapi.json`, `docs/roadmap/ai/cards/P10-A0-backend.md` |
| 2026-11-05 | **P10-A0 part 2 (Codex):** AI Autonomy/Activity pages and permission-filtered navigation; versioned rules, server-forced new-rule shadow state, limits/active switches, 30-day counts, confirmed pause/resume; Activity outcomes/reasons/current execution/result links and original grantor. Policy decision wording and delayed notification fix (`ai_acted` for `act_notify`, silence for `act`/other non-pending, shadow approval requests). Additive response fields; backend OpenAPI and TS regenerated by local generators. Final affected backend **120 passed**; affected modules + architecture **134 passed**; full frontend **309 passed**; Ruff/format/mypy/tsc/lint clean. `make check` dry-run only because it starts Docker; browser/screenshots/deployment untested. Only `smb_a0`; no installs, Docker, external calls, dependencies, deployment or commits. Owner review pending; evidence/file list in part-2 report | EVENT | `backend/app/modules/{approvals,notifications}/`, `frontend/src/features/{ai,approvals,notifications}/`, navigation/switch/i18n, generated contracts, `docs/roadmap/ai/cards/P10-A0-frontend.md` |
| 2026-11-05 | **P10-A0 typecheck correction (Codex):** the supervisor's `make check` found seven errors with `pnpm typecheck` (`tsc -b`); earlier `tsc --noEmit` did not check referenced projects. Removed unsupported role-query options while retaining default exact string-name matching; wrapped pause/resume error callbacks to pass only the error to `toastError`. No weakened tests, casts or suppressions. Requested `pnpm typecheck` and `pnpm lint` pass; AI/approvals/notifications Vitest **60 passed in 8 files**. Report corrected. No backend, dependencies, generated types, Docker, external calls or commits | EVENT | `frontend/src/features/ai/{AutonomyPage.tsx,autonomy.test.tsx}`, `docs/roadmap/ai/cards/P10-A0-frontend.md` |
| 2026-11-05 | **P10-A0 lifecycle integration follow-up (Codex):** deliver pending domain events immediately after proposal creation in `test_proposal_lifecycle_end_to_end`, while approval is still needed. Existing revision, approval, execution, audit, outbox, event-delivery and notification assertions remain unchanged; product behavior unchanged. Requested `tests/integration app/modules/notifications` run on `smb_a0`: **206 passed in 112.48s**. Changed-test Ruff/format and `git diff --check` pass. No Docker, installs, external calls or commits | EVENT | `backend/tests/integration/test_proposal_lifecycle.py` |
| 2026-11-05 ~08:15 | **P10-A0 verified by the supervisor:** backend diff reviewed (delegated policy decisions, execution-time re-checks under the proposal lock, atomic daily cap, no fabricated approver); `make check` 1,583 backend and 309 frontend tests, deploy checks, licences, no leaks — all passed after two Codex follow-ups (type errors in the new screen tests; one integration test that relied on notification timing) | EVENT | `backend/app/modules/approvals/`, `frontend/src/features/ai/` |
| 2026-11-05 | **P10-A1 backend (Codex, Tier A, independently reviewed):** contract first; explicit owned-thread chat approval with typed human provenance, current permissions and atomic PO money reservations; supplier PO email executor and second-person rules; delayed/stoppable AI outbox; independent delivery state; Work queue/Exceptions; purchasing copilot v2 and three synthetic evals. Migration `20261105_0900_chat_delivery`. Broad DB-only run **1,103 passed, 3 failed** (new-tool inventories/description), fixed; affected regression **163 passed**, final A1 **55 passed**. Ruff/format/mypy clean; OpenAPI regenerated. `make check` dry-run only (Docker/frontend excluded); **35 SMTP tests unrun** pending clarification of DB-only network rule. No installs, Docker, external providers, frontend changes, deploy or commit. Evidence/design/exact files in the A1 backend card; full phase stays for supervisor integration. | EVENT | `backend/app/modules/{ai,approvals,purchasing,notifications,org,marketing}/`, `backend/app/platform/registry.py`, migration, integration tests, backend OpenAPI, roadmap/module/runbook docs |
| 2026-11-05 | **P10-A1 screens (Codex, Tier A):** saved-thread action cards with exact revision/hash Confirm, Edit, local Not now, refusal wording, 409 refresh and execution/result polling; independent delivery states; shared countdown + confirmed Stop on cards, Activity, proposal detail and Work queue; paginated permission-gated Work queue/Exceptions, pause link; versioned 0–15 minute business send delay (default 2). Generated-field compatibility fixes preserve supplier `active` and update typed fixtures. **38 new MSW tests; full frontend 347 passed in 36 files (17.41s); `pnpm typecheck` (`tsc -b`) and lint pass; diff check passes.** Full `make check` dry-run only: Docker/DB/SMTP/deploy steps conflict with no-network/no-installs constraints; browser/live-backend/SMTP not verified. No backend/generated-schema changes, installs, dependencies, network, commits or deploy. Full file list and requirement evidence in [screens report](docs/roadmap/ai/cards/P10-A1-screens.md); full phase remains in progress for supervisor integration. | EVENT | `frontend/src/features/{ai,approvals,purchasing}/`, router/navigation/i18n; screens report, STATUS, EVENT_WORK |
| 2026-11-05 ~16:07 UTC | **P10-A1 supervisor SMTP follow-up (Codex):** independently reproduced 3 failures. Stale test setup retried immediately despite the existing one-minute next_attempt_at backoff; first delivery claims succeeded and manual mail had no AI delay. Product unchanged. SMTP tests now freeze app time, assert a claim one microsecond before retry due is skipped without state/attempt/SMTP DATA changes, advance to the persisted due time and retain original MIME/stable-ID/verified STARTTLS/implicit-TLS assertions. Targeted **3 passed**; exact requested `app/modules/notifications tests/integration` **261 passed in 147.11 s**, including all 35 SMTP tests. Ruff/format/mypy and diff check pass. Only localhost SMTP/test DB; no Docker, installs, external provider, frontend edit, commit or deployment. Full make check remains supervisor-run. | EVENT | `backend/app/modules/notifications/{tests/test_smtp.py,README.md}`, `docs/roadmap/ai/cards/P10-A1-backend.md`, `docs/roadmap/phases/STATUS.md` |
| 2026-11-05 ~17:25 UTC | **P10-A1 supervisor live-eval follow-up (Codex, Tier A, independently reviewed):** contract first; authenticated copilot staff quantities are bounded, copied from the triggering persisted request, shown with provenance, and used in PO snapshots/chat money caps. Suggestion-only background behavior and strict A0 policy validation remain; non-suggested quantities fall back before autonomy reservations. Copilot v3 and final-step schema distinguish requester Confirm from genuine human blockers; fresh confirmation eligibility informs the model. Mismatch eval now requires actual 10; new no-quantity case requires suggested 5. Exact requested offline AI/approvals/inventory/purchasing/integration suite **1,110 passed in 780.62 s**; architecture **17 passed**; Ruff/format/mypy pass; OpenAPI regenerated. Budget-test fixture uses stable test_agent.v1 without changing limits/assertions. No new migration, dependencies, external calls, frontend edits, commit or deployment. Live provider rerun and full make check remain supervisor-owned. Full implementation/file/evidence notes in backend card. | EVENT | `backend/app/modules/{ai,approvals,inventory,purchasing}/`, `backend/app/platform/registry.py`, copilot eval cases, integration tests, backend OpenAPI, contracts/module READMEs/backend card/STATUS |
| 2026-11-05 ~17:47 UTC | **P10-A1 second live-eval follow-up (Codex, Tier A, independently reviewed):** contract first; no-quantity copilot reorder arguments equal to the computed suggestion are accepted with suggestion provenance; other invented quantities return an actionable same-turn retry instruction. Explicit and ambiguous item quantities cannot enter suggestion recovery; paired need/negation guards remain strict. Unshipped v3 and FinalStep clarify that complete stock/supplier answers remain needs_human=false despite advice or warnings, with two examples. Exact requested offline AI/approvals/inventory/purchasing/integration suite **1,120 passed in 758.64 s**; architecture **17 passed**; Ruff/format/mypy and diff check pass; backend OpenAPI regenerated. Full make check dry-run only (Docker/frontend/deploy excluded); live eval remains supervisor-run. No new migration, dependency, frontend edit, external call, commit or deployment. | EVENT | `backend/app/modules/ai/{tools/staff_quantities.py,prompts/copilot.v3.md,agent/protocol.py,README.md}`, three A1 quantity/order integration test files, contracts/backend card/STATUS |
| 2026-11-05 ~18:15 | **P10-A1 verified by the supervisor:** backend reviewed (chat confirm only for the requester's own copilot proposal, exact revision/hash, fresh permission, second-person rules, atomic $500/order and $1,000/day chat limits; PO e-mailed to the supplier; outbox `not_before` and Stop). Live copilot eval on Vultr `mimo-v2.6-flash` after two prompt/tool rounds: **83 % of 24** (best so far; new ordering cases pass; injection resistance 100 %); the four remaining failures are older stock/supplier cases where the model still sets needs_human on low-stock answers. Found live: an explicit "order 10" was turned into the suggested 5 — fixed (staff-requested quantity; autonomy rules never act on a non-suggested quantity). `make check` 1,685 backend and 347 frontend tests, deploy checks, no leaks; `make e2e` 30 passed | EVENT | `backend/app/modules/{approvals,ai,purchasing,notifications}/`, `frontend/src/features/ai/`, `backend/evals/` |
| 2026-11-05 UTC | **P10-C1 part 1 backend (Codex):** contract-first M19 knowledge, immutable published snapshots/simple full-text search, public-only intake citations and permission-gated copilot internal reads; deterministic redacted suggestions and hand-over; visitor/staff ownership controls, generation-bound queued intake/replies, assigned/unassigned notification, deterministic opening-hours copy and backend disclosure. Migration `20261105_1000_knowledge_handoff`. Requested local pytest scope **1,102 passed in 791.92 s**; final focused rerun **55 passed in 30.79 s** after outcome-copy/eval refinements. Ruff/format/mypy and OpenAPI pass; restricted make-check lint/types, 347 frontend tests, licenses and gitleaks pass (Docker DB startup/backend target/deploy checks skipped; not an unrestricted full pass). C1 portable fixture rows validate; full fixture validator still encounters pre-existing missing autonomy_rules SQLite table (fixed in part 2 by the supervisor's decision; no owner question needed). No Docker, installs, external calls, frontend edits, real keys, commits or deployment. Live eval/generated TS and later C1 parts remain supervisor work. | EVENT | `docs/roadmap/ai/cards/P10-C1-part1.md`, M19/AI contracts, backend knowledge/conversations/ai/approvals/org, seed/fixtures/evals/OpenAPI and module cards |
| 2026-11-05 UTC | **P10-C1 part 2 backend (Codex, Tier A):** contract first; independent structured grounding class, typed claims/source/number checks, fail-closed staff fallback and knowledge suggestions; A0 reply eligibility and shadow demo rules; AI web replies withheld until durable publication with atomic Stop, generation/source freshness and cancellation Exceptions. Subsequent visitor input advances the generation independent of timestamp/UUID order. Portable fixture autonomy table fixed and validator added to make check. Migration `20261105_1100_grounded_publication`. Initial full scope 1,184 passed/26 failed (payload/script expectations and one reorder failure-code regression); fixes and review regressions: 55 passed. Clean requested full scope **1,219 passed in 903.27 s**; final parent-deadline guard then verified with **97 focused tests in 79.72 s** (the full scope was not repeated after that guard). Final Ruff/format/mypy/OpenAPI/fixtures/diff checks pass. Restricted make check passed twice (347 frontend tests, lint/types, fixtures, licenses, no leaks; Docker/backend Make target/deploy checks skipped; not an unrestricted pass). No Docker, installs, external calls, frontend edits, commit or deploy. Live/part 3 remain supervisor work; existing regulated router policy preserved pending E0. Final evidence: [part 2 card](docs/roadmap/ai/cards/P10-C1-part2.md). | EVENT | backend AI/approvals/conversations, routes/evals/migration/tests, fixtures/Makefile and roadmap/module READMEs |
| 2026-11-05 UTC | **P10-C1 part 3 screens (Codex):** Knowledge authoring/version history/suggestions, visitor AI disclosure and hand-over/system-message styling, Inbox ownership/countdown/Stop/cancellations, reply citations/grounding, front-desk Autonomy and Exceptions. Small API gap: public is_system flag; OpenAPI/TS regenerated. Final frontend typecheck/lint/build pass; **380 tests in 39 files** pass. Widget transitive JS 77,557 → 77,722 bytes gzip (+165). Offline backend projection/API architecture **17 tests** pass. Restricted make check attempted without Docker/backend DB/deploy targets; latest attempt blocked by 10 unrelated lint findings in newly appeared test_intake_live_regressions.py, left untouched. No installs, network/provider calls, commits or deploys performed here; DB/browser/a11y/unrestricted checks unverified. | EVENT | [part 3 card](docs/roadmap/ai/cards/P10-C1-part3.md), frontend knowledge/widget/conversations/AI/approvals/i18n; conversations public DTO/projection and generated schemas |
| 2026-11-05 UTC | **P10-C1 knowledge data protection follow-up (Codex, Tier A):** fixed admin export test's explicit module set to include knowledge. Verified existing registered export/erasure handlers through real admin APIs: all article snapshots exported, customer-linked and visitor-email suggestion questions tombstoned across statuses, unrelated/foreign rows and business article versions preserved; CRM ErasureTarget and collected links needed no change. Added daily per-business knowledge.retention: done/dismissed questions strictly older than configured retention_days_conversations (default 730 days) since updated_at; 500-row skip-locked batches and coalesced, scoped one-minute continuations. Review identified persistent regulated holds; added conversations public eligibility subquery and a regression proving actual restriction survives a switch to standard while unrelated expired rows are purged. Initial scope stopped at 322 passing to include that guard; final requested admin_ops/knowledge/crm/tests scope **344 passed in 231.10 s**; hold/retention/architecture focus **24 passed**. Ruff/format/mypy (316 sources), unchanged fresh OpenAPI and diff checks pass. Restricted make check passed (fixtures 149 rows/30 tables, licences, no leaks; lint/types/tests run separately, frontend/Docker/deploy checks excluded; not an unrestricted make pass). Read-only second-model review: no remaining blocker. No migration, HTTP schema/frontend edit, Docker, installs, inference, commit or deploy. | EVENT | backend admin_ops tests, knowledge lifecycle/registration/retention tests/README, conversations public retention helper/README, dependencies.py; docs/roadmap/{02-architecture.md,modules/M19-knowledge.md} |
| 2026-11-05 ~18:40 | **P10-C1 verified by the supervisor:** knowledge base v1 (M19), hand-over enforced (unassigned hand-overs now notify staff), cited front-desk answers, grounding check on a different model class (`grounding` → deepseek), front-desk replies under an autonomy rule in shadow mode, withheld web-chat replies with Stop and stale/human-mode cancellation, fixture validator added to `make check`. Live evals on Vultr after three Codex prompt/tool rounds: **intake 98.5 % of 67** (best so far; first C1 run 82 % — DB-fact answers had stopped being proposed; fixed), **grounding 100 % of 10** (unsupported precision and recall 1.0; a 30/thirty false positive fixed), injection 100 %, time zones 100 %. `make check` 1,822 backend and 380 frontend tests, all passed; `make e2e` 30 passed | EVENT | `backend/app/modules/{knowledge,conversations,ai,approvals}/`, `frontend/src/{features,widget}/`, `backend/evals/` |
| 2026-11-06 ~13:00–13:25 | **0.1.6 released to the VM** (P10-A0, A1, C1: autonomy rules, confirm in chat, PO e-mail, stoppable AI mail, knowledge base, hand-over, grounded front-desk replies). `make check`: 1,822 backend and 380 frontend tests, deploy checks, licences, no leaks (`make e2e` passed earlier on the same commit). Images built for amd64 and checked (four migrations in the API image, new screens in the web image), pushed; staged deployment files identical to the VM by checksum; manual backup, then `upgrade.sh 0.1.5 → 0.1.6` in 34 s, smoke passed, database at `20261105_1100_grounded_publication`. Route file with the new `grounding` class uploaded **after** the upgrade (0.1.5 rejects the class, so a rollback with it in place would run without AI; CHANGELOG corrected); api/worker restarted, no route errors. `reset-demo` was refused by the agent's safety check (deletes data), so the 11 demo articles and 4 shadow autonomy rules were added to the existing demo businesses with an add-only script using the seed's service calls. **Live check** with synthetic visitors: AI greeting shown; a warranty question → safety, intake v5 (3 steps, 2.5 s), grounding on deepseek (3 claims supported, 1.1 s), reply cited the published article and waits for approval (reply rule in shadow: `would_act`); Talk to a person after hours → system message, conversation in human mode, the pending intake stopped (`ai.conversation_paused`). SSH closed, Terraform no changes | EVENT | `CHANGELOG.md` (0.1.6), VM `/opt/smb` |
| 2026-11-06 UTC | **P10-E0 routing half (Codex, Tier A):** contract first; regulated permits only hosted:false + vendor:local with verified private resolved addresses, for all eight classes/fallbacks. Legacy baa still loads but warns and grants nothing; invalid unhosted vendors rejected. Effective policy rechecked per attempt, after budget waits and before accepting results; safety/grounding/repairs share the boundary; copilot preserves verified inner-router resolution. AI status adds per-class reasons; backend OpenAPI, local example, runbook and deployment comments updated. Read-only second review findings fixed and verified. Final **1,023 AI tests passed in 917.08 s**, **2,010 full backend tests passed in 1282.24 s** (xdist absent; sequential fallback); Ruff/format (606 files)/mypy (317 sources) passed. Restricted make check passed: 380 frontend tests, fixtures, licenses, no leaks, deploy regressions 105 tests with 19 skips; Docker DB startup/backend Make target and offline runtime checks excluded, not an unrestricted make pass. No installs, external provider calls, real keys, Docker, commits or deployment by this task. Live server/network/modality verification separate; System UI placeholder unchanged, diagnostics via /ai/status. Unrelated AGENTS/P10 decision edits and local-model eval reports preserved/unassessed. Full file/test/limit evidence: [routing card](docs/roadmap/ai/cards/P10-E0-routing.md). | EVENT | backend AI gateway/runner/model_calls/copilot/status/tests/OpenAPI/README; deploy local example/config comments; roadmap/contracts/handoff/runbook/STATUS |
| 2026-11-06 ~13:30–14:40 | **P10-E0 benchmark (supervisor):** all seven eval suites against `gpt-oss-20b` on llama.cpp (M1 Pro 32 GB, Metal), nothing sent off the machine. Grounding 100 %, extraction 93 %, summary 70 %, injection 100 %, copilot 17 %, intake 43 %, time zones 0 %; agent runs 36–43 s p50 (prompt processing ≈ 350 tokens/s). Decision recorded in `08-ai-layer.md`: under `regulated` only local grounding and extraction pass; agent use cases and summaries stay off until a local model passes on the clinic's hardware. Owner gave standing approval to commit and release (AGENTS.md); D5 = (a), D7 = 120 s / 4 MB recorded in the P10 plan. Routing change reviewed (policy, private-address check, per-attempt recheck); `make fixtures lint typecheck` passed | EVENT | `backend/evals/reports/2026-11-06-local-models.md`, `docs/roadmap/08-ai-layer.md` |

## 2026-11-06 — P10-D voice dictation backend (Codex)

Contract/M14 amended first for D7 (120 seconds / 4 MiB), then metadata-only STT/voice pipeline, writer polish,
scoped dictionary/guards, offline synthetic eval suite, microphone header and tests.
No frontend edits, installs, downloads, provider calls, Docker, commits or deployment.
Verification: final isolated smb_voice full suite 2,083 passed (1141.30 s); focused 82 passed;
restricted make check passed (380 frontend tests; deploy 106 tests / 19 offline skips); Ruff/format/mypy,
OpenAPI and native caddy validate passed. Initial failed run and the test-move setup errors are recorded
in docs/roadmap/ai/cards/P10-D-backend.md. Live evals, actual STT servers/codecs, browser UI, independent
review and deployment remain unverified. No commits were made.
| 2026-11-06 ~15:50 | **P10-D backend verified by the supervisor:** live `dictation` eval 87.5 % (24 cases, threshold 85 %, p50 0.8 s, guard fallbacks 0; the 3 misses are currency/thousands formatting); real audio through the new gateway (WebM/Opus and MP4/AAC, synthetic 8 s clip) transcribed by Vultr `whisper-large-v3-turbo` in 1.0–1.3 s; reviewed upload cap (streamed, 4 MB, magic bytes), polish guard and memory-only replay (transcripts never stored); TS types regenerated; `make fixtures lint typecheck` passed | EVENT | `backend/app/modules/voice/`, `backend/app/modules/ai/dictation*.py` |

## 2026-11-06 — P10-D staff dictation screens (Codex)

EVENT frontend-only implementation: reusable recorder/button and opt-in Textarea, assistant/Inbox/knowledge
and staff prose fields, cursor/selection insertion without sending, raw review, permissions/STT status and
plain-word errors. Frontend typecheck/lint/build pass; 424 tests in 40 files; total JS/CSS gzip +4,978 bytes.
Offline Playwright tests added, but Chromium launch denied by macOS sandbox; browser checks unverified.
Final restricted make check: fixtures/lint/types/frontend/deploy/licenses pass; Gitleaks flags
backend/app/modules/ai/chains.py:25 (generic-api-key). Backend not edited; matched value not printed.
No installs, external network/provider calls, real keys, commits or deployment. Evidence and outstanding
verification: [P10-D screens card](docs/roadmap/ai/cards/P10-D-screens.md).

## 2026-11-06 — P10-B1 backend AI team (Codex)

EVENT backend implementation completed after kickoff: contract first; seven-agent registry and tenant settings,
causal workflow metadata, fixed hand-off graph, atomic chain and agent/day budgets, disabled-policy fallback,
scoped copilot and team/chain/evidence APIs. Builds on committed voice. Only the supplied `smb_team` database used.
Full backend suite: 2,120 passed (96% total coverage); scoped make check passed backend lint/format/mypy/tests,
fixtures, backend licenses and secret scanning. Final targeted run: 40 passed in 18.63 s. Second-agent Tier-A
review performed and findings fixed. Frontend/types, browser, live models/delivery and deployment not verified here.
No installs, downloads, external calls, Docker, commits or release. Details: [P10-B1 backend card](docs/roadmap/ai/cards/P10-B1-backend.md).

## 2026-11-06 — P10-B3 AI Team screens (Codex)

Built Team cards, agent detail/cursor timelines, chain views, targeted assistant chips and Activity/Autonomy/Work
queue filters against the supplied generated API schema. Frontend changes only; no backend edits, installs,
external network calls, commits or release. Read/write gates, confirmation, version conflicts, USD validation,
delivery evidence and retry target retention have MSW coverage. Full command results, files, bundle comparison
and explicit API gaps (depth usage, Customer Care capability, assistant-only discovery) are in
[the P10-B3 card](docs/roadmap/ai/cards/P10-B3-screens.md). Scoped offline make check passed; database/runtime
checks are excluded as documented. TypeSafe was considered; these screens use deterministic backend fields.

P10-B3 final verification: 42 frontend files / 451 tests passed (27 new); typecheck/lint/build passed; total
production JS+CSS+HTML gzip growth 9,779 bytes. No live/browser acceptance claim; API gaps remain in the card.

## 2026-11-06 — P10-B1 backend review fixes (Codex)

Fixed the four independent-review findings: domain-event subscribers retain chain budgets/depth without
inventing agent hand-offs; supplier extraction resolves durable request lineage, including interrupted caches;
execution recovery selects the exact current revision/attempt; rejected admission follows the existing budget
counter lock order. Added eleven regression cases across three integration files. Before fixes, six SQL cases
and three cache cases failed; after fixes, all seven SQL and all four cache cases passed. The first full run
passed 2,127 tests; final full verification after the cache guard passed **2,131 tests in 1,488.31 s**, 96%
coverage. Scoped `make check` passed: Ruff/format/mypy, backend tests, fixtures, backend licenses and secret
scanning; frontend/Docker/deployment checks excluded. Regenerated OpenAPI matches the existing schema. Only
`smb_team`; no frontend edits, installs, downloads, external calls, Docker, commits or deployment. Details and
final verification: [P10-B1 backend card](docs/roadmap/ai/cards/P10-B1-backend.md#review-fixes).
| 2026-11-06 ~18:30–19:10 | **0.1.7 released to the VM** (E0 local-only routing, voice dictation, AI Team). `make check`: 2,131 backend and 451 frontend tests, licences, no leaks; `make e2e`: 30 passed (the offline dictation test was wrongly picked up by the main run and is now excluded). Images checked (two migrations, microphone header, new screens) and pushed; only `compose.yml` differed (comments only, copied); manual backup; `upgrade.sh 0.1.6 → 0.1.7` in 29 s, smoke passed, database at `20261106_1000_ai_team`; route file with the `stt` class uploaded after the upgrade, no route errors; `Permissions-Policy … microphone=(self)` live. In-container check: Vultr Whisper transcribed the synthetic clip in 2.4 s; the AI Team lists 7 agents (Customer Care off until B2). SSH closed, Terraform no changes | EVENT | `CHANGELOG.md` (0.1.7), VM `/opt/smb` |

## 2026-11-06 — P10-B2 backend Customer Care (Codex)

Implemented the owner-requested post-sales slice on committed B1/review fixes: disabled-by-default care agent,
check-ins/recalls, complaint/warranty human triage, reviewed message executor, delayed and stoppable mail/web
publication, live consent/preference/dispute/quiet-hour checks, minimal regulated templates, causality/budgets,
export, migration, synthetic seed rules and 19 offline eval cases. Only `smb_team` migrated/tested. Existing native
ARM runtime/extensions verified; no installs/downloads, external API/model calls, frontend changes, Docker,
commits or release. Full scoped make check passed: 2,225 backend tests in 1,648.55 s, 96% coverage, Ruff/format, strict mypy,
151 synthetic fixture rows, backend licences and Gitleaks. All 19 final offline evals passed again in 37.01 s.
Database head and forced RLS independently read back. Live model/delivery, frontend/types, independent review
and release remain separate. [B2 card](docs/roadmap/ai/cards/P10-B2-backend.md) owns files, decisions and evidence.

## 2026-11-06 — P10-B2 independent review fixes (Codex)

Reproduced all seven owner-supplied findings with failing tests before product edits (21 failures in 10.65 s).
Fixed request-only versus proactive consent, approved ownership snapshots/invalidation, independent bounded
triage despite paused/open-proposal Front Desk admission, anonymous-case binding under customer lock, current
recall rechecks at execution/delivery, Chinese review text and invoice tenant/customer/appointment validation.
Preserved the owner's eval runner/case edits and added answer_any unit coverage. Final focused verification:
167 passed in 78.64 s, including 35 review regressions. An email ownership boundary reproduced two more
failures before its shared execution/delivery guard; the earlier full run was interrupted after 592 passes.
Final full scoped make check: **2,261 passed in 1,667.80 s**, 96% coverage; Ruff/format/mypy, fixtures, backend
licences and Gitleaks passed. OpenAPI regenerated; existing B2 migration edited and only smb_team
downgraded/upgraded; final DB head/receipt columns/forced RLS verified. No installs,
downloads, external calls, Docker, frontend changes, commits or release. User-reported hosted eval 19/19 is
preserved but not independently rerun. Details: [B2 Review fixes](docs/roadmap/ai/cards/P10-B2-backend.md#review-fixes).

## 2026-11-06 — P10-B2 reviewer verification (Claude)

Live hosted evals (Vultr): `customer_care` first 14/19; the five failures were single required language words
(model used synonyms such as "turno"/"预约") and review-request cases demanding facts the prompt does not require.
Added an `answer_any` synonym-group expectation to the runner; rerun **19/19** (p95 5.1 s, $0.010). Intake with the
new safety v2 classifier dropped to 89.6 %: repair requests describing a symptom ("my brake squeaks, please book")
were classified as complaints and handed over. Tightened safety v2 with an explicit service-request vs complaint
boundary; refund case now accepts reply or direct hand-over (complaints go to a person per B2); added three
complaint/warranty cases (en, zh). Intake **69/70 (98.6 %)**, injection **15/15**. Also made
`preferred_contact_channel` optional on customer create/update (the generated TypeScript type made it mandatory,
so the edit dialog would have had to send "email" and could overwrite an opt-out). Full `make check` passed
(2,261 backend, 451 frontend); affected tests rerun after the prompt/case edits.

## 2026-11-06 ~22:30–22:45 — 0.1.8 released to the VM (Claude)

Customer Care backend (P10-B2). Images built from a clean worktree at `08e9b54` (the dental task was editing the main
checkout), checked (migration `20261106_1200_customer_care`, safety v2 boundary) and pushed. `make check` passed at
the commit (2,261 backend, 451 frontend); `make e2e` **not run** for this release (the dev stack serves the main
checkout with the dental work in progress; no screens changed). Deployment and route files identical on the VM; manual
backup; `upgrade.sh 0.1.7 → 0.1.8` in 29 s, smoke passed. In-container live check: 7 agents, Customer Care off;
safety v2 classified "my rear brake squeaks, please book" as `care_category=none` and a complaint about earlier
brake work as `complaint/high`. SSH closed, Terraform no changes, readyz 200, pending charges $6.70.

## 2026-11-06 — P10-E1 synthetic dental configuration (Codex, local only)

Implemented provider/chair scheduling with an exclusion-protected occupancy ledger and no-backfill migration;
conservative synchronous/classifier emergency hand-over, urgent Work items and deterministic clinic instructions;
15-patient clinic fixtures, existing B2 six-month recall in shadow, backend legal banner and regulated marker
refusal; service/emergency settings and allocation/banner UI. Contracts/M06 were updated first. No clinical
features or inference-provider changes. Focused dental/demo-login verification: 49 passed in `smb_dental`. Full backend verification blocked
by PostgreSQL tmpfs storage exhaustion; host has ~185 GiB free. No other databases were removed.
Evidence, pending owner storage input and independent-check results are in `docs/roadmap/ai/cards/P10-E1.md`. No installs, downloads, external calls, Docker, commits or deployment.

P10-E1 independent checks completed: 460 frontend tests; ruff/format, mypy (360 app source files),
frontend typecheck/lint, OpenAPI/types, 283-row/33-table fixture validation, license checks and gitleaks
passed. Offline deployment checks ran 106 cases with 19 skips; full backend/make check remain blocked
by the observed PostgreSQL storage error. Detailed requirement evidence and changed-file list are in the card.

## 2026-11-06 — P10-E1 reviewer follow-up (Codex)

Reviewer reports shared tmpfs space restored and reserves full suites for sequential reviewer runs.
Fixed synthetic_demo_exists search_path to place pg_temp last; downgraded/upgraded only smb_dental
and confirmed head. Reproduced the intake privacy test's missing registry in the existing inbound
message event publication path (before emergency handling); supplied the missing registry test fixture.
Updated intended boolean context, v7 with retained v1–v6, empty legacy resource list and emergency
DTO expectations. Nine reported cases plus regulated startup regression: 10 passed in 8.71 s.
Ruff/format (686 files) and mypy (360 source files) passed. Full backend suite and make check were
not run in this follow-up; next full verification is reviewer-run. No commits or external calls.

## 2026-11-06 — P10-B2 screens: Customer Care (Codex)

Separate `p10-b2-screens` worktree, baseline `e8bfd00`. Normal Team switch and readable Care reasons;
versioned Care settings (backend delay limit 365, quiet hours, tone); https public links in Business settings;
Work case detail/priority/known-or-unknown customer/related links and confirmed versioned Resolve; CRM contact
preference omission and consent scope; Care proposal previews and preserved-facts subject/body revisions.
[Full card](docs/roadmap/ai/cards/P10-B2-screens.md) lists files and requirement evidence.

Final native ARM verification: **494 frontend tests / 45 files**, typecheck/lint/build, scoped offline
`make check` (frontend + fixtures/licenses/secrets; backend/database/deployment excluded). Gzip bundle grows
**5,336 bytes vs main**, below ~12 KB. Existing chronological Work cursor API permits priority sorting only
within loaded pages; documented/UI-labelled. Browser/live API, e2e and delivery untested. No backend,
generated schema, service editor, manifests or lockfile edits; no commits or release.

First pnpm check unexpectedly attempted its automatic install/registry requests (DNS failures); stopped it.
Final commands disable dependency verification, force offline mode, and reuse existing native dependencies
with local writable caches. No successful installation/network response is claimed. See card for details.

### 2026-11-06 — P10-C2a (Codex, isolated worktree)

Contract first; embedding gateway/budgets, public published chunk jobs, filtered RRF retrieval and FTS fallback,
PDF draft-import backend/UI, provenance, export/erasure handling and eight offline paraphrase eval cases.
Owner-set D4/D9 preserved: pypdf only, custom vector(1024), Vultr dimensions 1024, no stored uploads or re-ranking.
Migration applied and cycled only on `smb_c2a`; reviewer rechains at merge. Native ARM runtime/tools verified;
existing developer tools reused read-only, no installs/downloads/API calls/Docker/commits/release.
Owner/reviewer follow-up: Linux PDF AS limit mandatory; native darwin development skips AS only with one structured
warning, retains all other caps, and adds no watchdog. Reviewer reports freeing shared tmpfs; independently checked
`smb_c2a` at migration head, no repair needed. Final focused **66 passed in 16.01 s**, including real subprocess
PDF cases, platform policy, retrieval races, AI lane and admin export. Scoped Ruff/format and mypy passed.
Prior frontend 454 passed. Earlier full check interrupted by DiskFull/recovery (760 passed, 26 failed, 43 errors);
per owner, no full backend/make check rerun. Full merge gates, Linux-image limits and live evals remain reviewer work.
See `docs/roadmap/ai/cards/P10-C2a.md`.

## 2026-11-06 — P10-C2b embed and customer self-service (Codex)

Implemented tenant CSP embed routing, vanilla loader (1,680 bytes gzip), iframe header-token chat, magic-link
self-service/Strict idle sessions, fee acknowledgment, canonical rescheduling, notifications, simulated-mail owner/admin
view, RLS migration/exports/erasure and frontend/settings/MSW coverage. No installs/downloads, external calls, Docker,
commits or deployment. Focused backend 46 pass; frontend 482 pass; lint/format/mypy/typecheck/build/API regen pass.
Full make check blocked: PostgreSQL DiskFull during existing knowledge teardown, then schema-restoration errors;
1,976 passed, 74 failed, 684 errors. Readback at 23:50 UTC: smb_c2b at 20261028_1200_regulated_hardening (38 MB).
Owner asked to restore database storage; no repair attempted. Independent offline deploy checks (108 tests,
19 skipped), backend/frontend licences and Gitleaks pass. Live Caddy/browser/SMTP and full re-verification remain.
Files, decisions, requirements/evidence and follow-up: [P10-C2b card](docs/roadmap/ai/cards/P10-C2b.md).

## 2026-11-07 ~00:30–01:15 — merge of P10-E1, B2 screens, C2a, C2b and 0.1.9 released to the VM (Claude)

Merged the three branches onto the dental commit (conflicts: Work queue item kinds, en.json deep merge, org settings
fields, scheduling export, seed order, shared docs); migrations chained customer_care → 1300 knowledge_embeddings →
1400 dental → 1500 self_service. Full backend run on the merged tree: 2,354 passed, 31 failed from two merge
interactions (settings round-trip rejected the new read-only banner; local example lacked an enabled `embed` route),
fixed in `6e3ec8f` (affected modules 190 passed); 537 frontend tests, fixtures, deploy checks, licences, gitleaks;
`make e2e` 30 passed. Live evals: intake 75/76, injection 15/15, grounding 10/10, customer_care 19/19 (two wording
checks widened), retrieval 8/8. Linux image check: PDF worker runs and RLIMIT_AS refuses a 2 GiB allocation.
VM: files identical, vector 0.8.7 present, manual backup, `upgrade.sh 0.1.8 → 0.1.9` in 32 s, smoke passed, route file
with `embed` uploaded after the upgrade. Live: Example Dental Clinic seeded (add-only); 13 public articles indexed with
Vultr embeddings, cost recorded per business; paraphrase search finds "Bike storage over winter" / "Insurance accepted"
where full-text finds nothing; embed CSP allows only the configured origin (cycle: https://example.com), unknown and
unconfigured shops get `frame-ancestors 'none'`, a spoofed X-Embed-CSP request header is ignored; self-service enabled
for the three demo businesses. Not verified: Safari framing in a real browser, real e-mail delivery (no SMTP).
SSH closed, Terraform no changes, readyz 200, pending charges $7.40.

## 2026-11-07 UTC — P10-gaps (Codex; owner request November 6 locally)

Contracts first; implemented disjoint emergency/Care-priority/ordinary Work pagination and gated display names,
measured chain/activity depth, registry triggers/switch capability, and minimal ai.assistant.use discovery for
assistant @ and Manager-off. Updated screens, generated API/types, focused tests and reviewer-run self-service,
widget/CSP/sessionStorage and dental emergency specs, including Ellery's seeded dev-account login helper.
Evidence, decisions and file inventory: docs/roadmap/ai/cards/P10-gaps.md. No migration or new dependency.
Focused checks: first backend regression 48 passed; expanded backend 42 passed; frontend 47 passed before the
last legacy reason formatter removal. Ruff/format/mypy and frontend typecheck/lint pass. Full make check is
running once at the end on isolated smb_gaps, bypassing Docker startup and using documented offline deployment
checks. Playwright is deliberately not run (Docker/reseed prohibited). Native ARM tools/bindings verified;
stale local pnpm .bin paths repaired without installs. No installs, downloads, external calls, Docker, commits,
cloud actions or deployment. Final aggregate evidence will be recorded in the gap card and below.

P10-gaps final verification (2026-11-07 UTC): full backend invoked exactly once, 2,397 passed / 1 failed in
2,097.87 s, 96% coverage. The failure was the exhaustive AI endpoint permission inventory (26 entries versus
27 routes); added the new addressable route and its assistant-only access assertion. No production code changed
after that full run. Complete safety-boundary file plus gap cases: 30 passed in 28.62 s after correction.
Full frontend: 539 passed / 52 files in 27.46 s. Final scoped make check (-o backend-test -o frontend-test)
passed Ruff/format (713), mypy (375), frontend typecheck/lint, fixtures (283 rows / 33 tables), offline deploy
regressions (108 tests, 19 skipped), both licence checks and Gitleaks. The unrestricted full check is not claimed
green and was not rerun. smb_gaps read back at 20261106_1500_self_service (head); no migration added. E2E
specifications remain unexecuted by explicit owner instruction. P10-gaps card and STATUS mark the implementation
done under these recorded conditions; hosted/browser verification and release remain outside this task.

## 2026-11-07 — P10-gaps reviewer widget UX follow-up (Codex)

Reviewer reports real cross-site Chromium send/reload/CSP checks succeeded before this follow-up. Fixed the
reported UX defects: loader v1.0.1 mounts only after valid successful business config, catches failures silently,
keeps its closed shadow root, and places iframe below a nonshrinking header through flex layout. Embedded
heading receives preventScroll focus. Updated widget.e2e.ts for absent loader on refused origin, a separate
CSP iframe probe, and 1280/375 px heading/header/frame geometry; send/sessionStorage/settings restoration kept.
Contracts updated first; module README, handoff, STATUS and P10-gaps card updated. No backend edits this turn.
Pre-fix unit tests reproduced 12 failures; final focused widget 60 passed / 5 files (2.43 s), final full frontend
554 passed / 52 files (24.08 s). Types/lint and pnpm build pass. Built loader matches source byte-for-byte:
4,365 raw / 1,714 B gzip (<=5,000); initial widget static JS graph 76,875 B gzip (<=80,000), native arm64 Node.
Frontend-scoped make check passes fixtures/lint/types/full Vitest/secrets; backend, deploy and licences excluded.
No Playwright run, backend tests, installs, downloads, external calls, Docker, commits, publishing or deployment.
Post-fix painted layout and cross-site browser acceptance remain reviewer checks. Evidence: P10-gaps card.

## 2026-11-07 ~02:30–03:20 — P10 gaps closed and 0.1.10 released (Claude)

Codex closed the screen-card API gaps (server-side Work queue priority bands, measured chain depth, agent triggers and
switchable flag, assistant-only `GET /ai/agents/addressable`) and wrote three e2e specs. Reviewer cross-site Chromium
check of the widget (host pages on localhost:5555 allowed / :5556 not): chat works in the iframe, survives a host
reload, CSP refuses the disallowed host; it found two UX defects (launcher shown on disallowed hosts; header covering
the chat), fixed by Codex. `make check`: 2,398 backend (96 %), 554 frontend, deploy checks, licences, gitleaks;
`make e2e`: 35 passed (new self-service, widget, dental specs). 0.1.10 upgrade 28 s, smoke passed; live: embed config
CORS answers only the configured origin, addressable endpoint requires sign-in. Not verified: Safari/WebKit (browser
not installed; download needs owner approval). SSH closed, Terraform no changes, pending charges $7.52.

## 2026-11-07 — widget verified in WebKit (Claude)

With the owner's approval, downloaded Playwright WebKit 27.2 (78.9 MB). The widget spec passes in WebKit at 1280 and
375 px: iframe chat, conversation resumes after a host reload (sessionStorage in a cross-site frame), no launcher on a
disallowed host, CSP refuses a direct frame. Two findings: Safari's default Tab key skips buttons, so the spec opens the
launcher with Option+Tab in WebKit (what Safari keyboard users press); the public 429 text promised "wait a minute"
while the conversation-start limit is 5 per IP per hour, now "Please try again later". Dev-only: cleared the local
dev stack's conversation-start counters after repeated test runs from one IP.

## 2026-11-07 — submission documents and real Vultr prices (Claude, Codex Terra)

Codex Terra rewrote `docs/submission/README-judges.draft.md`, `slides-outline.draft.md` and `docs/demo-script.md`
for 0.1.10 (the README described the assistant as planned). Reviewer restored the demo script's failure moments,
recovery rule and detailed dental / widget walkthroughs that the rewrite had dropped. Independent cold read (GPT-6.1
Sol, README text only, no repository access requested): architecture, Vultr use and the pre-event/AI split explained
correctly; its clarity notes (missing retrieval report, price wording, dictation model role) fixed. Re-ran live
retrieval 8/8 and grounding 10/10 for committed reports (the merge-time reports were lost with the merge worktree).
Route file now carries Vultr's own catalog prices from `GET /v1/models`: input and output priced separately (earlier
files charged input at the output price) and Whisper at $0.00001111/s = $0.000667/min instead of the $0.006/min
placeholder; costs recorded before 0.1.11 are overstated. Route, transcription and deploy tests: 276 passed.

## 2026-11-07 ~04:40–05:20 — 0.1.11 released (Claude)

`make check`: 2,398 backend, 554 frontend, deploy checks, licences, gitleaks. Images built from `e369347`, pushed; files
identical; manual backup; `upgrade.sh 0.1.10 → 0.1.11` in 29 s, smoke passed; route file with Vultr catalog prices
uploaded after the upgrade, api/worker restarted healthy. Live: a safety call on mimo-v2.6-flash (635 in / 32 out tokens)
was recorded at 72 micro-dollars, matching the catalog ($0.10 / $0.25 per million). SSH closed, Terraform no changes,
pending charges $7.64.

## 2026-11-07 ~08:20–09:00 — Name, author e-mail and publication prep (Claude, owner decisions in chat)

The owner named the project **Tandem Business AI by OpenDL** (repository `tandem-business-ai`) and asked for the
author e-mail `lpy@tuta.io` instead of the university address. Before anything was pushed, all 37 commits were
rewritten with `git filter-branch` (author and committer e-mail only; file trees identical, dates kept); the old
history is kept in `.git/backup-before-email-rewrite-20261107.bundle`. Four commit hashes quoted in this log and the
P10-B2 screens card now use the rewritten hashes (for example 0.1.11 images: `9d08c9b` → `e369347`). The judges'
README is the root `README.md`. Product name changed in the README, app title and sidebar, sign-in realm display
names, API title and runbook/roadmap headings; internal identifiers (`smb-*` images, realm id, Terraform, cloud-init)
are unchanged so the demo VM is not rebuilt.

Demo moved to **https://tandemai.open-dl.com** (owner's Cloudflare zone `open-dl.com`; the owner wrote `opendl.com`,
which is a different, non-resolving domain). A record → 45.63.85.83, DNS only (saved by the owner's approval in the
browser pane). On the VM: `.env` copy (the script's extra manual backup called `./backup.sh`, which does not exist in
`/opt/smb`, so it did nothing; the latest backup before the switch was the scheduled one at 12:00 UTC), Keycloak `smb-app` redirect URIs/web origins/post-logout URIs
updated with kcadm, `DOMAIN`/`PUBLIC_URL` changed, `docker compose up -d`; Let's Encrypt certificate issued, smoke
passed (issuer `https://tandemai.open-dl.com/auth/realms/smb`), login page accepts the new redirect URI, old sslip
address no longer served. Database had only three already-sent simulated messages mentioning the old address. SSH
closed (only 80/443 in the Vultr firewall), `terraform plan` no changes. Procedure added to the install runbook.

## 2026-11-07 ~13:40–14:20 — Sign-in name, dental logins, 0.1.12 released (Claude)

Live Keycloak realm `smb` display name and client names set to Tandem Business AI with kcadm (the realm file is
imported only once). Logins created with `demo-logins --business example-dental-clinic` for Ellery, Devon and Rowan
(passwords only in the private local `install-smb-demo/demo-logins.txt`; the VM copy deleted). 0.1.12 (rename) images
built from `8b7c8b6` after `make check` passed on the rename (2,398 backend at 96 %, 554 frontend, deploy checks,
licences, gitleaks); realm and example AI config files uploaded; `upgrade.sh 0.1.11 → 0.1.12` in 31 s with its own
pre-upgrade backup, smoke passed; live page title "Tandem Business AI" and sign-in title "Sign in to Tandem Business
AI". SSH closed, Terraform no changes, pending charges $8.21.


### 2026-11-07 — P10-G answer guardrail (Codex, EVENT)

Implemented owner-written automatic reply rules, independent safety-route checks with fail-closed payload evidence,
critical-topic autonomy reasons, rule-change execution/publication guards, Settings and Approvals explanations,
synthetic live/shadow/ask demo differences and 14 offline-harness eval cases. Added the recorded use-case constraint
migration after `20261106_1500_self_service`. No install, external service call, commit, deployment or route change.
Verification and remaining owner-run gates are recorded in `docs/roadmap/ai/cards/P10-G.md`.


### 2026-11-07 — P10-G public privacy follow-up (Codex, EVENT)

Added a server-derived public-profile auto-reply flag and hosted notice selection shared by public chat,
appointment requests and the website embed. Existing/private notices remain for the other configurations.
82 focused backend and 576 frontend tests pass; types/lint and backend static checks pass; API types regenerated.
Exact owner-supplied wording retained with the policy-scope discrepancy raised and recorded in the P10-G card.
No install, external service call, migration, deployment or commit.

## 2026-11-07 ~09:10–17:30 — P10-G answer guardrail; 0.1.13 and 0.1.14 released; customer-side demo video (Claude + Codex)

Owner asked for automatic answers to normal questions, staff approval for critical ones and a custom guardrail before
answering. Codex (Sol) built P10-G; Claude reviewed it, corrected the privacy wording (it claimed prices wait for a
person), added the new reasons to the eval harness and relaxed one injection case's autonomy assertion (the guardrail
may safely hold it). Full `make check` 2,443 backend (96 %), 576 frontend; live guardrail 14/14. Released 0.1.13
(backup, migration, smoke), then made Cycle Works' reply rule live (15-second delay, owner guardrail) through the
services as the owner.

Recording the customer scenes on the VM showed routine answers never went out: the assistant described a tune-up from
the service name (no knowledge search) and the fact check held both drafts (nothing wrong was sent), and guardrail v1
held any priced reply because it could not tell catalogue prices from invented ones. Fixed with intake prompt v8 and
guardrail prompt v2 (shared `REPLY_GUARDRAIL_VERSION`), new eval cases; live intake 76/78 twice, guardrail 16/16
twice; full `make check` again green. Released 0.1.14. On the VM, "What does your standard tune-up include?" was then
answered from the article with catalogue price and duration and sent after the 15-second window (25 s end to end);
the follow-up asking for 20 % off was held by the guardrail ("a person will reply").

Customer-side demo video (2:02, macOS voice) rendered from Playwright recordings of the public pages: Lena's chat,
Example Courier Co.'s request form, the dental emergency hand-over. Recording tools live in `frontend/output/video/`
(gitignored); storyboard in `docs/demo-video/storyboard.md`. Staff side waits for the owner to sign in.


## 2026-11-07 — P10-J public judge login (Codex, EVENT; main, uncommitted)

Owner-requested local implementation: Demo reviewer is exactly admin minus the three org writes and membership
write (48 permissions; complete enumerated set in the P10-J card). Added the role/schema/DB constraint migration,
fixture judge and three reset-safe memberships, dedicated demo-logins --judge, account-role/group/OTP removal and
old Keycloak session logout, explicit fixed-password repair under password history, and a demo-only theme with a
public credential asset rendered from DEMO_JUDGE_PASSWORD. Both realm imports retain their default theme.
No new dependency, installation, service, container, API/model call, cloud change, deployment, commit or push.
Existing VM commands, planned reset implications and activation/withdrawal steps are in the install runbook.

Final verification (2026-11-07, approximately 19:43 UTC):

- `make -o test-db check`: ALL PASSED **with the explicit scope below**. 2,422 backend tests, 95% coverage;
  584 frontend tests; 290 fixture rows in 33 tables; backend Ruff/format/mypy and frontend lint/types;
  offline deployment regressions 110 run (91 passed, 19 skipped), install dry run, licenses and gitleaks pass.
- Existing local `smb_j1` was used; no Docker DB startup. All 35 tests in notifications/tests/test_smtp.py were
  excluded to honor database-only network access. No test was persistently skipped or weakened.
- Focused backend command: `cd backend && .venv/bin/python -m pytest -q -x --tb=short -p no:cacheprovider
  app/modules/identity/tests/test_demo_judge.py app/modules/identity/tests/test_staff.py
  tests/integration/test_demo_logins.py tests/integration/test_seed.py` → 62 passed. The final broad run also covers
  the subsequent Keycloak-session logout assertion and admin/system/work-queue role-list updates.
- `cd frontend && pnpm vitest run` → 584 passed (also 9 focused identity/theme tests passed).
  `pnpm typecheck`, `pnpm tsc --noEmit`, `pnpm lint` → passed.
- `python3 -I data/validate_fixtures.py` → passed.
  `DEPLOY_CHECK_OFFLINE=1 DEPLOY_CHECK_NO_DOWNLOADS=1 bash deploy/scripts/check-deploy.sh` → passed with skips above.
- Gitleaks also passed against a staged theme rendered with an intentionally public synthetic test password;
  documented brace-expanded permissions were compared exactly against all 48 code permissions.
- Native arm64 manager/interpreters/Node and compiled psycopg/pydantic extensions verified; no runtime was installed.
- Untested: SMTP delivery; Docker Compose resolution, actual Caddy, Terraform providers and cloud-init schema;
  live Keycloak/browser/CSP, effective account roles, direct required-action/recovery lockout prevention and actual
  password-history repair; existing VM rollout. Faked Keycloak calls do not verify these live requirements.
  Public activation needs those live security checks. Judges share mutable records, so reset remains the recovery
  boundary; this change does not create per-judge isolation.

Exact scoped make invocation from the checkout root:

```bash
UV_OFFLINE=1 UV_NO_SYNC=1 UV_PYTHON_DOWNLOADS=never \
UV_CACHE_DIR=/private/tmp/tandem-judge-uv-cache \
UV_PYTHON="$PWD/backend/.venv/bin/python" \
PYTEST_ADDOPTS='--ignore=app/modules/notifications/tests/test_smtp.py -p no:cacheprovider -x --tb=short' \
DEPLOY_CHECK_OFFLINE=1 DEPLOY_CHECK_NO_DOWNLOADS=1 make -o test-db check
```

<details>
<summary>Changed files (complete local inventory)</summary>

- `.gitleaks.toml`
- `CHANGELOG.md`
- `EVENT_WORK.md`
- `README.md`
- `backend/app/cli.py`
- `backend/app/demo_logins.py`
- `backend/app/modules/admin_ops/tests/test_email.py`
- `backend/app/modules/admin_ops/tests/test_system.py`
- `backend/app/modules/identity/README.md`
- `backend/app/modules/identity/demo_logins.py`
- `backend/app/modules/identity/keycloak_admin.py`
- `backend/app/modules/identity/models.py`
- `backend/app/modules/identity/roles.py`
- `backend/app/modules/identity/schemas.py`
- `backend/app/modules/identity/tests/test_demo_judge.py`
- `backend/app/modules/identity/tests/test_staff.py`
- `backend/migrations/versions/20261107_1700_demo_reviewer.py`
- `backend/openapi.json`
- `backend/tests/integration/test_a1_work_queue.py`
- `backend/tests/integration/test_demo_logins.py`
- `backend/tests/integration/test_seed.py`
- `data/README.md`
- `data/fixtures.json`
- `data/schema.sql`
- `data/validate_fixtures.py`
- `deploy/compose/compose.prod.yml`
- `deploy/keycloak/themes/tandem-demo/login/resources/js/demo-credentials.js`
- `deploy/keycloak/themes/tandem-demo/login/resources/js/judge-login.js`
- `deploy/keycloak/themes/tandem-demo/login/theme.properties`
- `deploy/scripts/configure-demo-judge.py`
- `deploy/scripts/stage.sh`
- `deploy/tests/test_demo_judge_theme.py`
- `docs/roadmap/01-product-and-scope.md`
- `docs/roadmap/06-security-and-privacy.md`
- `docs/roadmap/ai/cards/P10-J.md`
- `docs/roadmap/modules/M01-identity.md`
- `docs/roadmap/phases/STATUS.md`
- `docs/runbooks/install.md`
- `frontend/src/api/schema.d.ts`
- `frontend/src/features/identity/hooks.ts`
- `frontend/src/features/identity/login-theme.test.ts`
- `frontend/src/features/identity/staff.test.tsx`
- `frontend/src/locales/en.json`

</details>


## 2026-11-07 — P10-J no-reset follow-up (Codex, EVENT; main, uncommitted)

Owner correction: the existing demo's video state must survive judge setup. This supersedes the prior P10-J
procedure's reset recommendation. The current install runbook explicitly forbids seed/reset as part of adding
this login to an existing VM.

`demo-logins --judge` now validates all three demo business slugs AND fixture ids before any app write, then uses
identity's seed-compatible ensure_user/ensure_membership path to add the claimable judge user and missing reviewer
grants/memberships. It reactivates only the judge's inactive membership, retains linked subjects and other members,
and leaves existing app rows unchanged on reruns. Even a valid demo --business selector still provisions all three;
non-demo selectors/ids and regulated profiles refuse before writes. Disabled app users and unexpected elevated judge
grants refuse rather than exposing an unsafe public login. Existing Keycloak setup/hardening behavior is retained.
No new migration, dependency, service/container, installation, model/API call, VM operation, reset, commit or push.
Fresh fixture seed/reset behavior is retained and covered by the seed suite on the isolated local test database.

Follow-up files:
- `backend/app/demo_logins.py`, `backend/app/modules/identity/demo_logins.py`
- `backend/tests/integration/test_judge_provisioning.py` (new)
- `docs/runbooks/install.md`, `docs/roadmap/ai/cards/P10-J.md`, `CHANGELOG.md`
- `backend/app/modules/identity/README.md`, `docs/roadmap/modules/M01-identity.md`, `data/README.md`
- `docs/roadmap/phases/STATUS.md`, `EVENT_WORK.md`

Verification (2026-11-07, approximately 19:55 UTC):

```bash
cd backend
.venv/bin/python -m pytest -q -x --tb=short -p no:cacheprovider \
  app/modules/identity/tests tests/integration/test_demo_logins.py \
  tests/integration/test_judge_provisioning.py tests/integration/test_seed.py
```

121 passed in 124.74 seconds. Includes 10 new provisioning cases: existing-install creation without reset;
second-run app id/row/version/timestamp stability; full snapshots of every non-judge row/table (including business
records and a customized guardrail) and unrelated members; missing-grant/membership repair and reactivation while
retaining a linked subject; missing/spoofed business and regulated/non-demo preflight; disabled/privileged judge refusal.
The four initial preservation/idempotency tests also passed separately. Keycloak calls were faked.
Verified existing native arm64 Python/uv and compiled psycopg/pydantic extensions; no runtime installed.

From the checkout root:

```bash
UV_OFFLINE=1 UV_NO_SYNC=1 UV_PYTHON_DOWNLOADS=never \
UV_CACHE_DIR=/private/tmp/tandem-judge-uv-cache \
UV_PYTHON="$PWD/backend/.venv/bin/python" \
make -o test-db -o backend-test -o frontend-test -o deploy-check check
```

ALL PASSED: fixtures (290 rows/33 tables), backend Ruff/format/mypy, frontend lint/typecheck, both license checks,
and gitleaks. Backend tests ran separately as above; the full backend, frontend and deploy suites were not rerun in
this follow-up. No Docker or network beyond the existing local `smb_j1` test database was used. Live VM/Keycloak
verification and the previously recorded lockout/CSP unknowns remain untested. No claim that the earlier broad test
run verifies this follow-up's changes; the new 121-test run is its backend evidence.

## 2026-11-07 ~17:30–20:50 — Staff-side demo video, 0.1.15 and 0.1.16 released (Claude + Codex)

Recorded the staff scenes in a visible Chromium window the owner signed in to (Morgan, then Riley); the session never
left that browser. Recording surfaced two bugs, fixed and released as 0.1.15 after a full `make check`: the staff
assistant was not given the business-local date (searched August for "next week"; new eval case, live assistant eval
23/25), and long approve labels/evidence pushed the Approvals page sideways. Final video `tandem-demo.mp4` (5:35,
1080p, Kokoro voice via the owner's local-neural-narration skill, offline): customer chat, fleet request, held draft
with guardrail reason and claim-level fact check, assistant stock and slot answers, AI reading a supplier email (every
number verified in the text), Riley approving and the code-ranked comparison, purchase order proposal, AI Team,
dental emergency, Vultr closing card. Kept out of git (`frontend/output/video/`).

Owner asked for a judge login with prefilled credentials and no one-time code. Codex built P10-J; Claude had the
setup changed so it never resets the demo, ran the full `make check` (2,467 backend at 96 %, 584 frontend) and
released 0.1.16. On the VM the judge (`judge@example.com`, Demo reviewer in all three businesses) was provisioned
without a reset, the realm uses the `tandem-demo` theme, and Keycloak shows no roles, groups or required actions and a
password as the only credential. The public sign-in page shows both fields prefilled (checked without submitting).
Not verified live: an actual judge sign-in and the Keycloak self-service paths (account console, password/OTP links),
which need someone to sign in; Claude does not sign in with passwords on the public host.

Owner verified 2026-11-07: clicking Sign in on the prefilled page signs in as the Demo Judge without a one-time code ("Works for me"). The account-console lockout check was not separately reported.

## 2026-11-07 — P10-K: synthetic clinic website (Codex, uncommitted)

Owner-requested work on `main`, without committing or deploying. Added the static Example Dental Clinic page and
one stylesheet, fixture-derived content, persistent synthetic/judge banner, public appointment/booking links and
the existing v1.0.1 widget loader. No assets, fonts, trackers, dependencies, services or containers were added from
outside the checkout. Files are baked into `/opt/demo-site/dental`, outside the staff app document root.

Optional `DEMO_SITE_DOMAIN` enables a separate Caddy host with automatic HTTPS. Compose maps empty/unset to an
unused named snippet, avoiding a route or certificate subject; Caddy's own default applies when the variable is
absent. The clinic has HSTS, nosniff, no-referrer, denied device permissions, Server-header removal and a strict CSP.
Missing files use an explicit 404 response. Final review removed the extra clinic error handler: Caddy sites share
listeners and the existing global prohibition on `handle_errors` protects upstream/capability error logging. That
original regression remains unchanged. The microphone tests independently assert the main and clinic policies.
The CSP permits the app origin for the loader/config/iframe and pins the exact
loader shadow-root stylesheet by SHA-256, without allowing inline scripts or arbitrary inline styles.

The dental fixture adds the allowed origin, with a regression that clears it and verifies reset restoration.
The owner's report that this origin is already allowed on the VM is preserved as a user report; this task does not
query the VM or reset its data. README, draft 0.1.17 changelog, installation runbook and roadmap card/status updated.

Validation so far: fixture validation (290 rows / 33 tables); 584 frontend tests / 53 files; `pnpm tsc --noEmit`,
frontend lint, backend Ruff/format; seed and dental integration suites 22 passed; deployment regressions 128 tests,
21 skipped (no listeners/Docker); final focused native Caddy/smoke suite 7 passed, including two additional smoke
tests added after discovery of the deployment suite. Native Caddy adaptation verifies the enabled host, absent
variable and Compose-disabled snippet, normal security headers, root isolation and exact widget style hash.
Local `docker compose config` resolution verifies empty/enabled values without starting containers. A static HTML
audit checks fixture names/durations, emergency/footer copy, approved resource URLs, landmarks and absent inline
styles/event handlers. Native arm64 uv, Python, Node, Caddy, Docker and psycopg extensions verified.

Constrained full check is running with installed tools and the supplied local `smb_k1` database:

```bash
UV_OFFLINE=1 UV_NO_SYNC=1 UV_PYTHON_DOWNLOADS=never \
UV_CACHE_DIR=/private/tmp/tandem-clinic-uv-cache \
UV_PYTHON="$PWD/backend/.venv/bin/python" \
PYTEST_ADDOPTS='--ignore=app/modules/notifications/tests/test_smtp.py -p no:cacheprovider -x --tb=short' \
DEPLOY_CHECK_OFFLINE=1 DEPLOY_CHECK_NO_DOWNLOADS=1 make -o test-db check
```

`-o test-db` uses the supplied DB instead of starting Docker; SMTP listener tests and live Caddy listener checks are
excluded under the network restriction. Unverified: image build, live Caddy HTTP/error headers, DNS/Let's Encrypt,
375 px browser layout, keyboard/a11y, live widget/chat/appointment/booking/judge flows, and the VM rollout. Unexpected
internal errors retain Caddy's existing potential Server-header limitation; this is not claimed fixed. The
adaptation and fake-curl tests do not establish those runtime requirements. No downloads or installs performed.

## 2026-11-08 01:30 UTC: 0.1.17 rolled back, 0.1.18 released (demo clinic website live)

- Owner moved networks; the Vultr API allow-list held only the owner's IPv6 range, so pushes waited until the Mac was
  back on that network (the allow-list is the owner's to change; Claude did not edit it).
- 0.1.17 upgrade on the VM rolled itself back twice on the clinic smoke check: first the new domain's certificate was
  still being issued, then `/` answered 404 because Caddy's `not file` matcher treats the site root directory as missing.
- 0.1.18 (commit d545f25): the matcher also tries `{path}index.html` (deploy test added); `smoke.sh` waits up to 90 s
  (`SMOKE_DEMO_WAIT_SECONDS`) for a new clinic domain's first certificate. Verified in the built image (`/` 200,
  missing page 404), deploy tests 129 passed, `check-deploy.sh` ALL PASSED (with Docker). Full `make check` not rerun:
  only deploy files changed; the last full run (0.1.17 tree) passed.
- VM: `DEMO_SITE_DOMAIN=tandemaidentaldemo.open-dl.com`, compose and smoke files uploaded (judge credential asset left as
  generated on the VM), manual backup, `upgrade.sh 0.1.18` OK, smoke passed including all six clinic checks.
- Dental `embed_allowed_origins` set to the clinic site through the org service as owner Ellery (audited). Browser check:
  the site loads, "Chat with us" opens the clinic chat with the synthetic-data and emergency notices, no CSP errors.
- `terraform plan` no changes; SSH closed; spend $9.49 of $200.

## 2026-11-08 16:30 UTC: demo video v2, submission materials, System page fix (0.1.19)

- Video v2 follows Example Dental Clinic: clinic website with embedded chat and emergency hand-over, judge sign-in and
  Ellery's first sign-in with authenticator setup (QR pixelated), Today briefing, inbox, summary and four-eyes block,
  voice dictation (synthetic spoken question fed as the recording browser's microphone; Whisper on Vultr), "@Purchasing"
  hand-off with chain limits, Inventory, Customer Care (recall facts; refuses to promise a refund without a policy),
  Marketing (consent exclusion, AI draft), Finance (Cycle Works reminder), AI Team, patient self-service reschedule via
  the simulated email link, audit log, owner policies and staff roles; then Cycle Works automatic answers. Local
  Kokoro narration. Full cut 9:38, short cut 6:09.
- Live demo data changed by the recording (synthetic): Priya Shah and Jordan Lee conversations, a quote request to
  Example Dental Supplies, campaign "Check-up reminder for autumn" (draft), Cedar Example's visit moved to Thu 12 Nov
  08:45 by self-service, Ellery's authenticator registered.
- Submission materials in `docs/submission/`: lablab form text, 10-slide PDF, cover image.
- 0.1.19: System page AI panel shows today's usage instead of the old "not installed" placeholder. Frontend 585 tests,
  type check and lint pass; full `make check` not rerun (frontend-only change).- 0.1.19 released 15:29 UTC: manual backup, upgrade OK, smoke passed, SSH closed, `terraform plan` no changes; pending
  charges $10.38, so the video, slides and form text now say "about $10". The new System panel was not checked in a
  signed-in browser after release (needs a staff login).

