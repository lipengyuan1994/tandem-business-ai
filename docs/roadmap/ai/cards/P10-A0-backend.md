# P10-A0 backend — delegated autonomy for reorder drafts

Status: backend implemented and locally verified (2026-11-05); supervisor UI/type integration and owner review remain.
Tier A / EVENT. Scope: backend, synthetic fixture additions and documentation. No frontend, deployment or commit.

Contract: [Autonomy rules (P10-A0)](../contracts.md#autonomy-rules-p10-a0), written before implementation.
Migration: `20261105_0800_autonomy`, directly after `20261104_0700_retention`.

## Result

The existing inventory threshold event queues the existing reorder-note job. The writer supplies explanation text;
inventory supplies quantity. AI proposals alone enter the one-action allowlist. A live rule records a delegated policy
approval, queues the existing executor and records an evaluation. Activity joins its current execution outcome and
links to the resulting reorder draft. Shadow, fallback, pause and revocation all use the same proposal lifecycle.

Rules, append-only evaluations and daily counters have forced business RLS. Policy decisions bind revision/hash,
rule/version, grantor and grantor roles without a human `decided_by`. Execution uses current grantor permissions and
rechecks delegation under the proposal lock. Policy audit entries carry rule/version and on-behalf-of grantor.

## Decisions within the requested design

- Kill switch: typed `business_settings.settings.ai_autonomy_paused`, default false. Public org functions lock/read
  and change just that field; the normal settings version, audit and settings-changed event are preserved.
- Authorization: existing `org.settings.write` (owner/admin); no new permission or dependency.
- Version compatibility is strict: every rule edit revokes queued policy approvals. This avoids inferring which edits
  are compatible. New rules always start shadow, even if the create request asks to act; a subsequent versioned PUT
  switches shadow off.
- Shadow successes reserve daily cap slots too, to measure realistic capped decisions. Failed checks do not reserve;
  execution failures do not refund. Rule edits do not reset a day's allowance.
- Inactive rules record `fell_back/rule_inactive`. Active `ask` and absent rules remain unchanged without evaluations.
  Active `off` raises before proposal insertion; there is no surviving proposal/evaluation after the transaction is
  refused. U4 logs and exits without retry, including when off changes during generation.
- There is no reorder-draft frontend route. Added the small permission-gated
  `GET /api/v1/inventory/reorder-drafts/{draft_id}` and used it as the working Activity result link.
- `find_open_proposals` gained opt-in `include_executing=False`. Only reorder callers opt in; invoice and other
  existing callers keep their old semantics. Proposal reads moved to `reading.py` to keep lifecycle writes small.
- Open draft means inventory status `draft`; `sent_to_purchasing` means converted. Sorted item locks serialize draft
  creation through both staff and executor paths. Stock/reorder-rule locks protect policy execution validation;
  stale stock/quantity or duplicate AI lines fail with `inventory.reorder_changed`. Staff retain quantity freedom.
- `act_notify` appears in Activity; this backend slice adds no extra outbound notification. Existing
  `ProposalCreated` notifications are retained; their `approval_needed` kind and the current human-approval UI
  wording should be handled in supervisor integration for already policy-approved proposals.

## Evidence against requested requirements

Tests use the real local PostgreSQL `smb_a0`, application-role transactions and owner-role setup/assertions. Model
responses are local `FakeGateway` scripts. No external service calls or installs were used.

| Requirement | Evidence |
|---|---|
| Manual proposals never rule-decided | `test_manual_never_uses_rule`, `test_off_refuses_ai_but_not_staff` |
| Live policy fields, no human approver, execution, draft, acted, audit, working Activity link | `test_policy_executes_with_real_delegation_and_activity` |
| Shadow, ask, absent rule, draft level, off | `test_shadow_and_unsupported_level`, `test_ask_or_missing_rule_unchanged`, `test_off_refuses_ai_but_not_staff` |
| Off ends U4 without retry; off race during model generation | `test_off_rule_skips_writer_without_retry`, `test_off_rule_changed_during_generation_is_handled` |
| Pause, removed roles, suspended membership, inactive rule at creation and execution; version changed to ask | parameterized `test_revocation` |
| Atomic daily cap, concurrent distinct-item proposals, business-local day reset | `test_concurrent_daily_cap_and_local_day` (6 threads, cap 3) |
| Entire threshold-event → event worker → U4 job → fake writer → policy → execution job → Activity; duplicate trigger and next-day intent | `test_low_stock_event_policy_execution_and_next_day_intent` |
| Existing open draft blocks proposals and executor; stale stock prevents execution | `test_open_draft_blocks_proposal_and_executor`, `test_stale_stock_fails` |
| Crash after writing rolls back; racing resumes create one draft | `test_crash_after_claim_rolls_back_and_resumes_once` |
| Cross-business rules/evaluations/counters/activity inaccessible | `test_api_permissions_version_conflict_and_rls` |
| Owner/admin vs operator, 409, all new endpoints require permission | `test_api_permissions_version_conflict_and_rls`, `test_activity_pagination_shadow_summary_and_admin_access`, expanded `test_every_ai_endpoint_requires_its_permission` |
| Immutable decision/evaluation app grants; cap/effects rollback together; duplicate-line defense | `test_evaluations_and_decisions_are_append_only`, `test_transaction_rollback_does_not_spend_cap_or_publish`, `test_duplicate_ai_lines_cannot_inflate_quantity` |
| Demo rules and existing data | existing module and integration seed tests; fixture comparison preserves every pre-existing table verbatim |
| Existing human decisions survive migration | populated legacy-schema smoke: original user retained, kind `user`, policy fields null, head restored |

Independent read-only review checked delegation, concurrency/lock order, tenancy, migration constraint naming and
in-memory OpenAPI. Its findings (result route, stock race, tool approval flag, duplicate lines) were fixed. No remaining
findings. The reviewer did not run database tests.

## Validation

- First full module run: **3 failed, 821 passed in 551.23s**. Failures were the invoice open-lookup semantics, an
  existing AI proposal setup that should remain valid before execution, and the API permission route sweep.
  All three were corrected without weakening the original checks.
- Focused affected modules and boundary tests: **112 passed in 45.51s**.
- Final requested module suite: **831 passed in 589.03s (0:09:49)**.
- Additional audit, DB RLS, module/route architecture, migration, seed and OpenAPI exposure checks:
  **1 failed, 42 passed in 27.83s**. The failure was the checked-in OpenAPI snapshot, intentionally not yet
  regenerated at that point. Generated only `backend/openapi.json` using `.venv/bin/python -m app.cli openapi`.
  Re-ran the failed environment-contract test and the architecture snapshot test: **2 passed in 2.41s**.
  No failing checks remain. The extra checks include a real **empty-database downgrade to base and upgrade to head**.
- Populated migration smoke in `smb_a0`: downgraded only A0, inserted a legacy human decision, upgraded to head;
  observed kind `user`, identical `decided_by`, null rule/grantor fields. Synthetic smoke rows removed; head restored.
- OpenAPI diff: exactly the five new paths (six operations), seven new DTO schemas, delegated decision fields and
  the typed business kill switch; no existing paths changed or disappeared.
- Ruff: **All checks passed!** Format: **514 files already formatted**.
- mypy: **Success: no issues found in 284 source files**.
- `git diff --check`: passed. Native runtime: Python and inspected psycopg/Pydantic extensions are arm64.
- `make check`: not run because it invokes Docker, frontend and broader project checks outside this run's restrictions.
- `make gen-api`: not run as instructed. In-memory OpenAPI includes all new routes and nullable decision user;
  backend OpenAPI snapshot was regenerated directly without Docker; frontend type regeneration belongs to the supervisor.
- Not tested here: live model/provider, deployment, browser/Activity UI.
  No production operation or frontend completion is claimed. The overall P10-A0 roadmap row remains in progress for
  supervisor integration/review.

## Files changed in this task

New:
- `backend/app/modules/ai/autonomy_api.py`
- `backend/app/modules/approvals/autonomy.py`
- `backend/app/modules/approvals/autonomy_schemas.py`
- `backend/app/modules/approvals/autonomy_service.py`
- `backend/app/modules/approvals/decisions.py`
- `backend/app/modules/approvals/reading.py`
- `backend/app/modules/approvals/tests/test_autonomy.py`
- `backend/migrations/versions/20261105_0800_autonomy.py`
- `docs/roadmap/ai/cards/P10-A0-backend.md`

Modified:
- `backend/app/modules/ai/README.md`
- `backend/app/modules/ai/api.py`
- `backend/app/modules/ai/tests/test_reorder_note.py`
- `backend/app/modules/ai/tests/test_safety_pass_boundaries.py`
- `backend/app/modules/ai/tools/proposals.py`
- `backend/app/modules/ai/tools/proposing.py`
- `backend/app/modules/ai/usecases/reorder_note.py`
- `backend/app/modules/approvals/README.md`
- `backend/app/modules/approvals/engine.py`
- `backend/app/modules/approvals/lifecycle.py`
- `backend/app/modules/approvals/models.py`
- `backend/app/modules/approvals/public.py`
- `backend/app/modules/approvals/schemas.py`
- `backend/app/modules/approvals/service.py`
- `backend/app/modules/inventory/api.py`
- `backend/app/modules/inventory/executors.py`
- `backend/app/modules/inventory/public.py`
- `backend/app/modules/inventory/service.py`
- `backend/app/modules/org/README.md`
- `backend/app/modules/org/public.py`
- `backend/app/modules/org/schemas.py`
- `backend/app/platform/audit.py`
- `backend/app/platform/context.py`
- `backend/app/seed.py`
- `backend/openapi.json`
- `data/README.md`
- `data/fixtures.json`
- `docs/roadmap/07-workflow-engine.md`
- `docs/roadmap/ai/contracts.md`
- `docs/roadmap/modules/M08-approvals.md`
- `docs/roadmap/phases/STATUS.md` (backend progress annotation only)
- `EVENT_WORK.md`

Owner's pre-existing ADR-020 changes and untracked P10 phase plan were preserved, not authored or rewritten here;
pre-existing STATUS edits are preserved too. No commits were made. EVENT-kind implementation/verification recorded on 2026-11-05 UTC.
