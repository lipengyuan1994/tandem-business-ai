# Batch G implementation record — EVENT, 2026-11-04 UTC

Status: **DONE — approved integration applied; requested checkout verification completed.**
The full AI run exposed one UUID-redaction failure, fixed and verified by a targeted rerun.
The full module was run once as requested; it was not rerun after the fix.
Recorded completion: 2026-11-04 04:55 UTC. Live rollout and SMTP receipt remain untested. The owner explicitly approved
editing `backend/app/modules/ai/gateway/router.py` and `deploy/compose/compose.prod.yml` in chat.
The approved integration was applied and its temporary patch file deleted as requested.

The shared ModelCaller now opens a call-scoped budget context; the router accounts for each actual
provider attempt, including fallbacks, without replacing routing decorators or mutating shared
adapters. Copilot disconnect checks and procurement validation/script bindings are preserved.
Agent and structured completion use the same path. No live deployment was performed.

Before applying, the current production Compose file was read and saved for comparison. Removing
only the seven added AI setting lines from the result reproduces that saved file byte-for-byte.
Batch F's API/worker `stop_grace_period: 120s`, PostgreSQL command/settings and all other existing
Compose content were preserved. Batch E's admin_ops and module lifecycle files were not edited.

## Requirement evidence

| Requirement | Implementation and tests | Required-condition status |
|---|---|---|
| Atomic business token reservation before calls | Conditional updates to `ai_usage_daily`; real PostgreSQL races admit exactly the calls that fit; rejected multi-counter transactions roll back completely | Connected; real PostgreSQL concurrency tests passed in the checkout |
| Global daily/monthly USD ceilings | `ai_cost_periods`, UTC keys, Decimal settings; simultaneous requests from two businesses cannot exceed either ceiling | Connected; real PostgreSQL concurrency tests passed in the checkout |
| Reconcile real/failed/timed-out calls | Refund only reported usage; retain unknown usage/crashes; fallback attempts reserve separately; reconciliation stays on original day/month across midnight | Checkout tests passed; full-run/rerun results below |
| Process concurrency cap | Fail-fast counter shared across ModelCaller users; rejected calls neither reserve nor contact providers; exception releases slot | Checkout tests passed; full-run/rerun results below |
| Public abuse controls | Atomic per-business UTC-hour admission at enqueue/execution; serialized queued/running-job count; stored messages get staff handoff and one note without safety/agent calls; denial survives event redelivery/job retry after reset | Checkout tests passed; full-run/rerun results below |
| Staff allowance | Staff never spends public hourly quota; public intake/safety token share capped at 25% of business tokens | Connected; checkout tests passed |
| 80%/100% notices | Atomic scope/period claims; notifications public API queues optional operator mail, including installation thresholds; real/simulated channel assertions | Outbox covered by tests; actual SMTP receipt untested |
| Migration | Budget schema round trip seeds installation spending from existing daily rows; tenant tables retain forced RLS | Migration regression passed in the checkout |
| Deployment wiring | Seven values merged into API/worker environment and excluded from other services; new parsed-YAML regression | Parsed-YAML regression and offline deploy checks passed; Docker resolution untested by instruction |

## Files changed by this batch

Under `backend/app/modules/ai/`:

- New `budget_models.py`, `budget_notices.py`, `model_calls.py`, `public_intake.py`, `reservations.py`.
- `usage.py`, `subscribers.py`, `usecases/intake.py`, `agent/runner.py`, `gateway/router.py`.
- New `tests/test_batch_g_budgets.py`, `tests/test_batch_g_notices.py`,
  `tests/test_batch_g_intake.py`, `tests/test_batch_g_migration.py` (36 cases).
- Existing `tests/test_runner.py`, `tests/test_intake.py`, `tests/test_safety_pass_boundaries.py`,
  `tests/test_safety_pass_structural.py`. Budgets now fit the first conservative reservation while
  retaining next-call refusal assertions. The domain-write snapshot treats the three new AI accounting
  tables like its existing AI exclusions; business snapshots, cross-business denials and
  `include_ai=True` checks remain intact. No existing test was skipped or weakened.

Other paths:

- `backend/app/platform/config.py`: seven new AI settings and required imports only.
- `backend/migrations/versions/20261104_0600_ai_hard_budgets.py`.
- `deploy/compose/compose.prod.yml`: seven AI environment values only.
- New `deploy/tests/test_ai_budget_settings.py`: merged environment coverage for both inference processes.
- `docs/roadmap/ai/contracts.md` §12, own additive 0.1.1 bullets in `CHANGELOG.md`, this implementation record.
- Deleted the applied temporary integration patch as requested.

Other agents' existing shared-file changes were preserved. No commits, dependencies, services or
containers were added.

## Final checkout verification

Native `.venv/bin/python`: `platform.machine() == "arm64"`; Python, psycopg extensions,
pydantic-core and shellcheck inspected as arm64. No interpreter or package environment was created.
Backend tests use the supplied application/migration URLs for `smb_fg` and `-q -p no:cacheprovider`.
A temporary `sitecustomize` disables Settings dotenv loading so no checkout `.env` is read.

The AI module was run once in the checkout after applying integration:

```sh
cd backend
export TEST_DATABASE_URL='postgresql+psycopg://smb_app:smb_app_dev@localhost:5433/smb_fg'
export TEST_DATABASE_MIGRATION_URL='postgresql+psycopg://smb_owner:smb_owner_dev@localhost:5433/smb_fg'
PYTHONPATH=/private/tmp/smb-g-no-env:$PWD .venv/bin/python -m pytest -q -p no:cacheprovider app/modules/ai/tests
```

Full-run result:

```text
1 failed, 682 passed in 417.31s (0:06:57)
```

The failure was `test_intake_customer_prompt_and_completion_canaries_absent_from_logs_and_steps`.
A randomly generated service UUID had a long numeric suffix. Redacting the already serialized
service JSON treated that suffix as a phone number, so `_rebuild_state` could not parse the ID.
The allowed intake file now redacts text values before JSON serialization, preserving typed UUIDs
and numeric facts. A deterministic regression uses numeric UUID suffixes and large numeric facts
while asserting phone/email masking remains intact. Matched-customer facts use the same safe path.

The affected modules were rerun after this fix, without repeating the full module run:

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider \
  app/modules/ai/tests/test_batch_g_intake.py \
  app/modules/ai/tests/test_intake.py \
  app/modules/ai/tests/test_intake_agent.py \
  app/modules/ai/tests/test_safety_pass_structural.py \
  app/modules/ai/tests/test_batch_b_regressions.py
```

```text
104 passed in 58.23s
```

No full-AI pass after the fix is claimed. The failure and the affected context/booking/safety
paths pass in the targeted rerun, including the deterministic reproduction.

Ruff, format and mypy:

```text
All checks passed!
486 files already formatted
Success: no issues found in 278 source files
```

Deployment checks use the current script's explicit offline mode:

```sh
DEPLOY_CHECK_OFFLINE=1 DEPLOY_CHECK_NO_DOWNLOADS=1 make deploy-check
```

A verified native Python is first on PATH. This runs all deploy unittest discovery, parsed-YAML
checks, shellcheck and installer dry run. It explicitly excludes Docker, live Caddy/listeners,
Terraform provider validation and cloud-init schema. Final result:

```text
Ran 99 tests in 67.219s
OK (skipped=19)
deploy checks: ALL PASSED
```

Shellcheck of all deployment scripts and the eight-step installer dry run passed. The 19 skipped
tests require unavailable Docker or live HTTP/listeners under the requested offline boundary.

`make check` was not run because it invokes the complete backend suite and Docker, contrary to the
requested verification boundary. No external network, Docker, Vultr, provider, real-key or SMTP
operation was performed. Runtime Compose resolution, live deployment and actual mail receipt
remain untested, not inferred from offline results.

## Operator-facing changes

1. In the maintenance window apply `20261104_0600_ai_budgets` before starting new API/worker.
   Previous spending seeds installation counters; no customer data reset. Existing intake jobs remain
   and drain under the execution quota. Batch E's retention migration follows this revision.
2. Installation limits default to **USD 5/day and USD 50/calendar month**, UTC, across all businesses.
   Each attempt reserves conservative UTF-8 input plus maximum output. Failed/unknown attempts and
   crashes retain the reservation; usage screens and notices include in-flight reservations.
   Accurate route prices, provider bill reconciliation and provider-side restrictions remain necessary.
3. `AI_MAX_CONCURRENT_CALLS=2` per process fails fast with `ai.provider_unavailable` when busy.
   One API plus one worker permits up to four installation-wide simultaneous attempts; database
   budgets are shared.
4. `AI_PUBLIC_INTAKE_PER_HOUR=30` per business per UTC clock hour and
   `AI_PUBLIC_INTAKE_MAX_PENDING=30` queued/running jobs. Over-limit messages remain visible with
   staff handoff and an explanatory note. Zero public quotas disable public AI, not message storage.
5. `AI_PUBLIC_DAILY_TOKEN_BUDGET_PER_BUSINESS=200000`, capped at 25% of business daily tokens,
   covers public intake plus safety. Staff does not spend the public hourly quota and can use the
   remaining business tokens. All use cases share installation financial ceilings.
6. Optional `AI_BUDGET_NOTIFICATION_EMAIL` receives deduplicated 80%/100% business and observed
   installation notices via the existing outbox. Without an address there are only in-app notices;
   staff emails cannot be obtained through the identity public API. Configure SMTP for real mail;
   otherwise the channel is explicitly simulated. Independently verify actual receipt.
7. Stage the updated production Compose file, set overrides in both protected environment copies,
   and recreate API/worker. Compose forwards all seven settings to both processes. Existing
   install/upgrade scripts require no new service or dependency. The outbox shares worker/SMTP
   failures, so keep independent operational monitoring.

8. Intake context redaction also preserves typed service/customer/part IDs and numeric values, so
   phone-like UUID suffixes no longer stop intake. Free-text contacts remain masked. This fix needs
   no additional migration or manual configuration.
