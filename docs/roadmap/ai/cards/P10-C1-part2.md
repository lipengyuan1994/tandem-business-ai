# P10-C1 part 2 — grounded replies, shadow autonomy and delayed publication

Tier A · EVENT · 2026-11-05 · Backend only. Status: **DONE for backend part 2**, with verification limits below.

Contract written first: [P10-C1 part 2](../contracts.md#p10-c1-part-2).
Migration: `20261105_1100_grounded_publication`, after `20261105_1000_knowledge_handoff`.
Part 1's existing uncommitted work is preserved. No commit, installation, Docker, external call or frontend edit.

## Behavior

- Every ordinary intake reply receives an independent `grounding` completion before the proposal transaction.
  Inputs are this reply, its actual run-returned cited passages, initial database context and all successful returned
  operational tool observations. Separate class/prompt/run, normal policy filtering, reservations and recorded usage.
- JSON object only, <=800 output tokens, required claims list with <=8 entries. A malformed/missing/over-cap result,
  error, timeout, policy block or budget failure becomes unavailable and retains staff review. Overall verdict is
  conservative. Claims/source allowlists and operational number/field/subject/currency checks run in code, reusing U4's
  complete-token validator. Money derives exact major-unit values from integer minor units. Prices cannot borrow a
  service duration, another named service's price, or raw minor units; duration cannot borrow buffer, and available
  stock cannot borrow on-hand/reserved. Compound/ambiguous numeric claims fall back conservatively.
- The typed result is part of the immutable reply payload and therefore appears on proposal detail. Unsupported
  or contradicted output creates a contact-redacted suggestion from the actual inbound question.
- `conversations.send_reply` joins A0's allowlist. Intake provenance, safety clean, supported/no-claim grounding,
  auto ownership, current generation/source inbound, open/unrestricted thread and no bundle are required before cap
  reservation. A0 policy decisions, grantor checks, pause, revision/hash binding and execution jobs are reused.
  Staff/manual and staff-assisted drafts stay reviewable; booking-with-reply remains a booking action requiring approval.
- Demo reply rules: act_notify, shadow=true, cap 10 per business/local day. Shadow results use existing Autonomy,
  Activity and notifications. Live rules require the existing separate versioned update.
- All AI-origin executor web replies, including manually approved booking replies, commit withheld with publish_at
  from A1's send delay and immutable source-message provenance. A durable `conversations.publish_reply` job releases
  them. Public reads exclude withheld/stopped/cancelled rows and order/cursor on release time to avoid skipped replies.
- Customer→conversation→message locks serialize Stop/publication and customer/staff mutations. Publication checks
  auto mode, ownership generation, latest visitor/customer message and closed/restricted/erased state. Cancellations
  carry reasons in staff Inbox and Exceptions; the visitor never sees them. Stop is idempotent; publication wins → 409.
  An early job reschedules; transaction rollback preserves retry. Manual replies remain immediate; mail keeps A1.
  Each subsequent visitor input advances the existing generation; tied timestamps/reversed IDs cannot hide newer input.
- Backend staff DTO includes publish_at, publication state, cancellation_reason, `Sending in m:ss` and Stop URL.
  Delivery projections add withheld/published/stopped independently of executor success. Work queue includes withheld
  replies; Exceptions includes automatic cancellations. UI rendering is part 3.
- Portable fixtures now include autonomy_rules and message publication fields; rules load after their references.
  Both demo businesses have reply rules. `make fixtures` is a prerequisite of `make check`.
- New ten-case grounding suite covers all requested labels and injections, with recall >=0.90 / precision >=0.80
  configured in suites/grounding.yaml. Failed/skipped/unavailable checks cannot inflate recall. Four intake knowledge
  cases also assert real would_act shadow outcomes. Fake results validate the harness, not live model performance.

## Requirement evidence

| Requirement | Evidence |
|---|---|
| Supported/unsupported/contradicted/no-claim, source membership, exact numbers/fields/currency, claim cap | ai/tests/test_grounding.py |
| Timeout/model error/budget/invalid output fail closed; >8 claims keeps staff proposal | test_check_errors_fail_closed; integration/test_grounded_replies.py |
| All returned observations retained, separate checker class and accounting | ai/tests/test_grounding_intake.py |
| Independent checker cannot authorize after parent intake deadline | test_checker_cannot_authorize_effect_after_parent_intake_timeout |
| Labelled suite, precision/recall and failure-denominator behavior | ai/tests/test_grounding_evals.py; evals/suites/grounding.yaml |
| Knowledge grounded/DB-price shadow decisions through real eval harness | ai/tests/test_knowledge_evals.py |
| Shadow eligibility/cap/fallbacks; manual/staff-assisted proposals; booking remains ask | integration/test_grounded_replies.py |
| Policy executor withholds; public visibility; due-time real worker; cursor; too-late Stop | test_policy_execution_withholds_until_due_and_public_cursor_does_not_skip |
| New visitor, takeover, closed thread cancel with reason and enter Exceptions | test_publication_cancels_and_exceptions_explain |
| Five threaded Stop/publish races, idempotency/permissions/scope | integration/test_reply_publication_races.py |
| Customer-before-thread lock order at policy execution and publication | test_linked_customer_lock_precedes_conversation_lock |
| Staff replies immediate | test_manual_staff_reply_immediate |
| Fixture validator and check prerequisite | make fixtures; Makefile check prerequisites |

## Verification

Native existing Python/uv/Node verified arm64; platform.machine()==arm64; pydantic_core and psycopg_binary compiled
extensions verified arm64. No environment created or extended. Database: only localhost:5433/smb_a0, using the
provided app role and owner migration role. Fake inference only; localhost SMTP tests are included in requested scope.

The requested complete local scope passed: **1,219 passed in 903.27 s**. It includes all requested modules,
notifications (including local SMTP/TLS), integration tests and architecture checks. Initial full run: 1,184 passed /
26 failed; repaired exact payload assertions, diagnostics and fake-script inventory, scoped old one-rule seed queries
by action, restored the reorder executor's existing stale-stock failure code, and supplied the checker's repair response.
No failing test was disabled or weakened.

```sh
cd backend
export TEST_DATABASE_URL='postgresql+psycopg://smb_app:smb_app_dev@localhost:5433/smb_a0'
export TEST_DATABASE_MIGRATION_URL='postgresql+psycopg://smb_owner:smb_owner_dev@localhost:5433/smb_a0'
.venv/bin/python -m pytest -q -p no:cacheprovider app/modules/knowledge app/modules/conversations app/modules/ai app/modules/approvals app/modules/notifications tests
```

A final small parent-deadline guard was added after that full run started. The full command was not repeated after
that addition. Its explicit regression plus all grounding, runner, publication, race and architecture tests are included
in the final focused run recorded below. The earlier focused review run passed **55 tests in 70.36 s**.
Final focused verification after the deadline guard: **97 passed in 79.72 s**, including the explicit parent-timeout
regression, all grounding tests/evals, repeated-tool accounting, runner, publication/races and architecture.

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider app/modules/ai/tests/test_grounding.py app/modules/ai/tests/test_grounding_evals.py app/modules/ai/tests/test_grounding_intake.py app/modules/ai/tests/test_knowledge_evals.py app/modules/ai/tests/test_runner.py tests/integration/test_grounded_replies.py tests/integration/test_reply_publication_races.py tests/architecture
```

Final Ruff check and format check pass (574 Python files); mypy app passes (315 source files). OpenAPI regenerated
with `.venv/bin/python -m app.cli openapi > openapi.json`; the final focused run includes the snapshot/route/DAG checks.
`python3 -I data/validate_fixtures.py` passes: **149 rows in 30 fixture tables**, including four autonomy rules.
`git diff --check` passes. Real route files validate with grounding's requested primary/fallback and JSON object / 800
cap; offline policy checks confirm normal filtering for the new class, including the documented BAA allowance.

Restricted `make check` passed twice, most recently with **347 frontend tests in 36 files**, backend/frontend lint and
types, fixtures, license checks and gitleaks (no leaks):

```sh
UV_CACHE_DIR=/private/tmp/p10-c1-uv-cache UV_NO_SYNC=true UV_OFFLINE=true \
  make check -o test-db -o backend-test -o deploy-check
```

The flags skip Docker database startup, the Make backend-test target and deployment checks; this is **not an
unrestricted make-check pass**. Backend verification is the explicit local PostgreSQL command above plus the focused
regressions. No environment synchronization or install was permitted. Frontend checks use the existing generated TS
schema; compatibility after regeneration and rendered new fields are untested here.


Independent read-only Tier A review identified and verified fixes for lock inversion, missing claims being accepted,
overwritten DB observations, numeric values borrowed from other fields/subjects, empty DB sources, numeric names,
no-claim questions with numbers and tied-clock/UUID freshness. The reviewer also inspected the final parent-deadline
and linked-action Stop additions and reported no blockers. Reviewer ran no DB tests; executed evidence is below.

## Decisions and limits

- No frontend changes: actual countdown/Stop rendering, regenerated TypeScript and browser/widget testing are part 3.
  Backend visibility filtering is tested; a rendered widget is not tested here.
- Preserved the existing router policy as requested. It still permits BAA-covered hosted providers under regulated;
  ADR-020's global local-only conversion is scheduled for E0. No hosted/real provider was called. Clarification was
  offered; this slice does not claim that the broader local-only ADR requirement is implemented or verified.
- Policy periods such as warranty days can be supported by the cited policy passage. Operational prices/hours/stock/
  service durations require exact corresponding DB fields. Ambiguous compound numeric copy stays for staff.
- Human hand-over paths retain part 1's deterministic immediate acknowledgement rather than running a checker for
  copy that is immediately invalidated. Flagged intake stops before drafting; unchecked safety prevents autonomy.
- Web publication has its own conversations Stop endpoint and registered delivery reader; no duplicate mail pipeline.
  An AI draft without an immutable inbound source fails closed as stale_reply at publication; direct manual replies
  remain immediate. Intake reply and booking tools retain their original inbound message evidence.
  No frontend dependency, service, package, permission or generic task system was added.
- Full unrestricted make check would start Docker/deploy tooling. The restricted invocation and skipped prerequisites
  will be reported explicitly; it is not equivalent evidence of an unrestricted pass.
- Live grounding/intake evals, deploy and independent owner review remain supervisor work.

## Files authored or extended in this part

```text
Makefile
EVENT_WORK.md
backend/README.md
backend/openapi.json
backend/app/platform/registry.py
backend/app/modules/ai/{README.md,grounding.py,grounding_numbers.py,models.py,schemas.py,work_queue_api.py}
backend/app/modules/ai/agent/runner.py
backend/app/modules/ai/gateway/{routes.py,fake.py}
backend/app/modules/ai/prompts/grounding.v1.md
backend/app/modules/ai/tools/{base.py,proposals.py}
backend/app/modules/ai/usecases/intake.py
backend/app/modules/ai/tests/test_grounding*.py
backend/app/modules/ai/tests/{test_eval_harness.py,test_intake.py,test_intake_agent.py,test_intake_v3.py,test_knowledge_evals.py,test_propose_tools.py,test_reorder_note.py,test_router.py,test_summary.py}
backend/app/modules/approvals/{README.md,autonomy.py,autonomy_service.py,reading.py}
backend/app/modules/conversations/{README.md,api.py,executors.py,models.py,publication.py,public.py,registration.py,replies.py,repository.py,schemas.py,service.py}
backend/app/modules/notifications/README.md
backend/evals/{README.md,runner.py,runtime.py,suites/grounding.yaml,cases/grounding/*.yaml}
backend/evals/cases/intake/kb-{warranty,returns,price,injection}.yaml
backend/migrations/versions/20261105_1100_grounded_publication.py
backend/tests/integration/{test_grounded_replies.py,test_reply_publication_races.py}
data/{README.md,schema.sql,fixtures.json}
deploy/config/{ai-routes.yaml,ai-routes.dev.yaml,ai-routes.example.yaml,ai-fake-script.dev.yaml}
docs/roadmap/ai/{README.md,contracts.md,HANDOFF.md,cards/P10-C1-part2.md}
docs/roadmap/modules/M04-conversations.md
docs/roadmap/phases/STATUS.md
```
