# P10-B1 backend — AI team

Tier A / EVENT, 2026-11-06. **DONE — backend; owner/reviewer verification and release separate.** Builds on committed P10-D
voice migration `20261106_0800_voice_dictation`. Contract: [P10-B1: AI team](../contracts.md#p10-b1-ai-team).

## Result

Seven code-owned agents wrap existing workflows. Versioned tenant settings control admission and delegated
execution. Existing agents default on; reserved Customer Care defaults off. The Manager's off switch disables
all copilot messages, including targeted messages. Global autonomy pause keeps its existing semantics.

Correlation, causation, producing agent and depth propagate through existing event delivery, jobs, AI runs,
proposals and execution, including delivery retries and supplier waiting. Fixed graph checks reject forbidden
handoffs before inference. Chain accounting bounds provider attempts, committed actions and cost; agent cost
limits share existing atomic installation/business reservations. No second task queue or inference service.

## Decisions

- Chain ceilings: depth 6; 30 provider attempts (repairs/fallbacks included); 10 committed actions; USD 0.50.
  Actions include human-approved AI proposals, holds and direct campaign revisions. Failed effects roll back
  their action reservation. Rejected chain ceilings persist a terminal reason; a manual proposal outside the
  stopped chain remains available to staff.
- Costs use the existing UTC reservation day and conservative prices; unknown usage retains its allowance.
  Agent ceilings include attributable legacy spend. Exhausted agents cannot open new runs and notify once per
  UTC day; raising a limit takes effect immediately. Nullable ceiling adds no agent-specific restriction.
- Missing settings mean version 0 and registry defaults; PATCH creates version 1. Shared/exclusive advisory
  locks cover absent settings and serialize disabling against automatic execution.
- Disabled producer policy execution returns to pending human review with a fresh exact revision, preserving
  the immutable old policy decision and preventing replay. Existing global pause/rule revocation is unchanged.
- Untargeted copilot keeps the Manager's existing permissions and tools. Targeting intersects a job-specific
  tool allowlist with current staff permissions and filters proposal context. Manager and target must be on.
- Procurement scans resume each subject's original chain; unlinked subjects have stable roots. Payment scan
  jobs use independent invoice/day roots, so unrelated invoices do not share one small chain budget.
- Settings, daily agent usage and chain counters export through AI; existing platform/proposal export includes
  new columns automatically. Erasure logic is unchanged. Timeline retention follows its existing source rows;
  accounting receipts remain conservative so retention cannot reset a chain budget.
- TypeSafe considered: admission, delegation, graph edges and money limits need deterministic checks; no
  semantic judgment, new provider or external call was added.

## Files

- `ai/agents.py`, `chains.py`, `team_{models,schemas,settings,budget,service,activity,api}.py`: registry,
  configuration, accounting, projections and four team/chain endpoints.
- `ai/agent/{contexts,runner,proposal_events}.py`, recording, reservations, tools registry, jobs/subscribers,
  affected use cases and `usecases/copilot_stream.py`: workflow boundaries, attribution and streaming cleanup.
- `platform/{context,db,models,events,jobs,registry}.py`: immutable scoped metadata and composition hooks.
- `approvals/{models,service,decisions,engine,autonomy,reading,public,team,lineage}.py`: metadata, disabled-policy
  fallback, action reservations and public operational projections without reverse AI imports.
- `notifications/service.py`, `purchasing/quote_service.py`: delivery and supplier-response continuity.
- `ai/lifecycle.py`, `migrations/versions/20261106_1000_ai_team.py`, `backend/openapi.json`: storage/export/contract.
- Tests: `ai/tests/test_team_{controls,campaign}.py`; `tests/integration/test_ai_team{,_chains,_budgets}.py`;
  `test_ai_team_migration.py`; export fixtures/expectations in AI helpers and `admin_ops/tests/test_data.py`;
  the existing full endpoint permission sweep includes all four new routes.
- This card, contracts and own appended STATUS/HANDOFF/EVENT sections. Frontend files left to the concurrent task.

## Requirement evidence

| Requirement | Evidence |
|---|---|
| Unique action ownership, known graph edges, compatible defaults | `test_registry_consistent`, `test_upgrade_defaults_and_rls`, team API default assertions |
| Disabled trigger/job, delegated execution/human fallback, Manager and targeted chat | `test_disabled_agent_skips_queued_jobs_and_triggers`, `test_disabled_inventory_returns_policy_action_to_human_and_human_can_execute`, copilot/tool-boundary tests |
| Event → job → run → proposal → execution → emitted event | `test_event_job_run_proposal_execution_emitted_event_chain` |
| Durable supplier and delivery chains, independent scan subjects | supplier reply, payment scan, delivery retry tests in `test_ai_team_chains.py` |
| Forbidden graph/depth, call/action/USD races, terminal stops and rollback | team controls, chain and budget integration tests |
| Agent/day limits, legacy cost, one notice, no new runs, UTC rollover | `test_ai_team_budgets.py` and agent limit integration tests |
| API shapes, exact permissions, optimistic race/audit, pagination, RLS | `test_ai_team.py`, full architecture RLS/route tests |
| Regulated unavailable without local agent route | local-writer-only availability test; E0 regression suite |
| Export, old rows stay nullable, migration reversible | AI/admin export tests and full migration downgrade/upgrade suite |

## Verification

Only `smb_team` on localhost:5433 is used, with the supplied application/migration roles. Native arm64 Python
and psycopg extensions verified. No install, download, live model/API call, Docker operation, commit or release.

Targeted verification: **40 passed in 18.24 s** (37 added tests, existing export/permission sweeps,
and OpenAPI freshness). Ruff reports all checks passed, 642 files already formatted; strict mypy reports no
issues in 341 source files. The initial full pass found only the existing export-table and endpoint-count
expectations requiring expansion (2 failed / 2,109 passed); both are fixed and passed in the targeted run.

Final full backend verification through scoped `make check`:

```text
All checks passed!
642 files already formatted
Success: no issues found in 341 source files
TOTAL  19412  749  96%
2120 passed in 1476.28s (0:24:36)
backend licences OK
no leaks found
make check: ALL PASSED
```

Command: `make -f Makefile -f /private/tmp/p10-b1-check.mk -o test-db -o frontend-test -o deploy-check check`,
with both TEST_DATABASE URLs and DATABASE URLs explicitly targeting `smb_team`. Temporary recipes select the
existing `.venv` binaries for backend lint/format/mypy/pytest/licenses; the original Makefile is unchanged.
Fixtures passed (149 synthetic rows / 30 tables). No Docker prerequisite or deployment check was invoked.

After correcting the team's measured autonomy-history readout and clarifying budget-notice wording, the same
40 focused tests passed again in **18.63 s**, including the new assertion that the team shows the actual acted
count. Ruff, formatting and mypy were rechecked on these final changes; OpenAPI freshness passed again.
The Tier-A second-agent read-only review was performed in two passes; its concrete findings were corrected
(disabled-chat admission, tool boundaries, action caps, legacy cost, durable stop reasons, supplier/scanner
lineage, failed-effect evidence, permissions and evidence links).

**Untested / not done:** unrestricted `make check` (frontend/deployment portions), frontend and TypeScript
generation (supervisor-owned), browser behavior, real model/provider delivery, live evals and deployment.
No backend requirement remains knowingly incomplete. Customer Care's post-sales triggers remain reserved for B2
as requested. Historical rows intentionally retain null tracking; no historical chain reconstruction is claimed.

## Review fixes

Independent GPT-6.1 Sol review, 2026-11-06. Changes are limited to the four reviewed paths, regression tests,
this record and an appended EVENT_WORK section; existing frontend work is preserved.

1. **Deterministic domain consequences (P1).** `ai/subscribers.py` now preserves correlation/depth and binds
   causation to the received event, while clearing inherited agent metadata before enqueuing each subscriber's
   work. The existing run factory assigns the reacting agent. Events retain their original producer for evidence.
   No graph edges were added: the fixed graph still governs agent-initiated hand-offs, not module-emitted state
   consequences. Regression: a Front Desk booking reserves stock through the real executor, crosses the reorder
   point, produces the Inventory reorder draft and Purchasing quote-request proposal in the same budget (four
   model calls, two executed actions). The depth-six control still blocks both subsequent runs.
2. **Durable extraction context (P1).** `_extract_reply` resolves the quote-request subject before constructing
   its factory. Stopped/depth-exhausted requests cannot start a fresh root. Interrupted extraction caches are reused
   only when correlation, causation, depth and agent match that resolved context; mismatched legacy output passes
   through normal admission/generation again. Matching caches retain their existing replay behavior.
3. **Exact crash recovery (P2).** `_claim` joins the exact revision/idempotency key and requires that revision to
   remain current. A failed policy attempt cannot hide a later human attempt. The regression performs a real
   disabled-agent fallback, human approval and simulated crash after TX-1, then verifies one successful execution.
4. **Budget-counter lock order (P2).** Counter writes take installation month → installation day → business
   `AiUsageDaily` → public allowance (when used) → agent/day counter (when used) → `ChainBudget`. Paths can omit
   counters, but must not acquire an earlier counter after a later one. Rejected admission now counts business
   usage before persisting its chain stop, atomically with the blocked run. Settings' shared advisory locks are
   separate from this counter order. The two-connection regression forces a reservation to hold business usage
   while rejected admission reaches that row, reproducing the original database error and proving terminal
   `AiError` handling after the fix.

Files: `ai/{subscribers,recording}.py`, `ai/usecases/procurement.py`, `approvals/engine.py`, and
`tests/integration/test_ai_team_review{,_locks,_cache}.py`. No API schema or migration change.

Regression evidence before fixes: **6 failed, 1 passed in 8.56 s** (the depth guard control passed); after the four
fixes: **7 passed in 8.52 s**. The additional interrupted-cache branch cases failed **3 / 4** before the lineage
check and passed **4 / 4 in 0.88 s** afterward. Those branch tests were run without database fixtures while the
existing SQL suite held `smb_team`; the final normal backend suite includes them with the usual test setup.

The first full pass, started before the interrupted-cache fix, passed **2,127 tests in 1,461.12 s** with 96%
coverage; scoped `make check` passed. Final full-suite verification on the final sources, including all eleven
regression cases under the normal database fixtures, passed using the same scoped command and explicit
`smb_team` URLs documented above:

```text
All checks passed!
645 files already formatted
Success: no issues found in 341 source files
TOTAL  19421  743  96%
2131 passed in 1488.31s (0:24:48)
backend licences OK
no leaks found
make check: ALL PASSED
```

Synthetic fixtures passed (149 rows / 30 tables). Regenerated OpenAPI was compared with `backend/openapi.json`
and is unchanged, so that file was left untouched for this review repair. Follow-up read-only review found no
remaining issue in the cache fix; that reviewer did not run tests. The scoped check excludes frontend, Docker
database startup and deployment checks; unrestricted `make check` was not run. No requested backend review fix
remains untested or knowingly incomplete.
