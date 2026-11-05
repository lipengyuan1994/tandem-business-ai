# P10-A1 backend — explicit chat approval and stoppable PO delivery

Status: backend implemented and locally verified (2026-11-05); localhost SMTP follow-up verified below; owner/supervisor review remains.
Tier A / EVENT. Scope: backend and documentation. Contract written before implementation:
[P10-A1: confirm in chat, PO delivery, send delay](../contracts.md#p10-a1-confirm-in-chat-po-delivery-send-delay).
Migration: `20261105_0900_chat_delivery`, after `20261105_0800_autonomy`.

## Result and design

- `purchasing.send_po` shares the manual send state rules and version checks. PO transition and one supplier
  outbox message commit with executor success. The new template includes business/PO/date, priced named lines,
  total/currency and default location/address; text and escaped HTML omit internal notes. Reply-To uses the business
  public email. No SMTP retains the existing simulated channel. Local `sent` is distinct from delivered.
- The supplier model had no active field. Added default-true `active`, including existing rows, with the existing
  versioned supplier API. Missing email, inactive supplier and already-sent PO have explicit domain errors.
- Executor metadata supplies second-person and money checks without approvals importing purchasing. Sending above
  50,000 minor units, in another currency or to a supplier with no prior sent PO requires another person; campaign
  approval always does. Normal requester approval still respects `allow_self_approval`, with second-person enforced
  even when that switch is enabled. Execution rechecks current authority and second-person eligibility for chat/self.
- Only the dedicated authenticated chat endpoint bypasses the generic self-approval switch: own thread, assistant
  origin/requester, linked copilot run with the same user, pending/unexpired, fresh permission and exact revision/hash.
  Its immutable human decision says `decided_via=chat`; other decisions default to `approvals`. No approval tool.
- Chat limits reserve per-business/local-date counters under a row lock. Source draft/quote/PO reservations count the
  highest amount once per day across create→send, with atomic top-ups; distinct orders share 100,000 minor units.
  Reservations roll back with failed confirmation and are not refunded for execution failure or Stop. New tables
  have forced RLS. PO creation is also checked against 50,000 per order and business currency.
- Thread cards include preview, current status, revision/hash, eligibility/reason, delivery and whitelisted result
  UUIDs. Later turns receive current cards/results as fenced context, so the copilot can continue the PO chain.
  Each tool call proposes exactly one executor; each step waits for explicit confirmation and execution.
- Existing U2 exclusions were a catalogue scope restriction. Purchasing quote-request/quote/PO/follow-up tools now
  join the normal permission filter; lookup, quantity and evidence validation remain. Payment-reminder scope is
  unchanged. `copilot.v2.md` keeps v1. Three synthetic eval cases cover order intent, quantity mismatch and typed yes.
- Notifications resolves origin through approvals' public reader. AI-origin email/simulated mail gets
  `outbound_send_delay_seconds=120` (0–900), regardless of human/chat/policy decision; staff mail has no delay.
  The existing job is scheduled at not_before; claim checks both delay and retry time under the row lock.
- Stop explicitly scopes the RLS-exempt outbox, authorizes the action's approver or owner/admin, and locks
  pending→cancelled. Sending/terminal/unknown is too late; repeated Stop is idempotent. Audit/event are transactional.
  Targeted delivery waits for locks: a rolled-back Stop must not cause a delivery job to finish as a skip.
- Delivery aggregation uses notifications' registered reader, preserving the module DAG. Priority is unknown,
  failed, sending, scheduled, stopped, delivered; no messages is none. Proposal execution status is independent.
  Detail, cards and A0 Activity expose delivery. Activity still lists autonomy evaluations; it does not become a list
  of all chat actions. A0's rule allowlist remains only reorder drafts.
- Work queue merges pending unexpired proposals (permission/eligible roles), scheduled mail and unanswered sent
  quote invitations with reply_by. Exceptions merges failed proposals, failed/unknown deliveries and seven-day
  fallback evaluations, plus current pause. Both require proposal read, scope every source and paginate a unified
  newest-first timestamp/UUID order. Contacts, outbox variables and capability URLs are excluded.
- Copilot thread DTOs/reads/retention moved to `copilot_threads.py`; card provenance to `copilot_cards.py`, keeping
  streaming orchestration below 400 lines. Cross-module A1 tests live under tests/integration. Module cards ≤80 lines.
- Downgrade refuses to discard chat decision provenance, stopped delivery history, pending send delays or inactive
  supplier state. Legacy user decisions retain their real decider and default to `decided_via=approvals` on upgrade.

## Requirement evidence

Tests use the real local PostgreSQL `smb_a0`, application-role transactions, real sessions/current identity checks,
owner-role fixtures/assertions and scripted FakeGateway responses. Native Python, psycopg and Pydantic extensions
were verified arm64. No new dependencies, runtime installation, Docker, external provider calls, commit or deploy.

| Requirement | Evidence |
|---|---|
| Supplier email, PO sent, one outbox, retry/no duplicate, escaped body/no internal notes | `test_send_po_queues_supplier_once_and_marks_sent`, `test_send_po_missing_supplier_email_is_clear`, `test_supplier_deactivation_before_execution_keeps_po_and_mail_atomic` |
| Second person: >500, other currency, new supplier; proposal stays pending | `test_second_person_refused_in_chat_stays_pending`, `test_send_po_second_person_metadata` (each parameterized) |
| Own thread/user/run provenance | `test_confirm_provenance_cannot_cross_user_or_thread` (six cases) |
| Stale revision/hash 409; refreshed locked state | `test_confirm_revision_or_hash_mismatch_409`, `test_cached_revision_cannot_confirm_after_competing_revision`, `test_two_concurrent_clicks_on_same_card_decide_once` |
| Fresh approve permission, human chat marker, ordinary self-approval denied | `test_missing_current_approve_permission_403`, `test_chat_confirm_marker_and_normal_self_approval_still_blocked` |
| Concurrent daily cap, local midnight reset | `test_concurrent_daily_money_limit_and_business_local_reset` (six threads, two 500 approvals) |
| Create→approve→send exposure once; create above per-order cap refused | `test_full_copilot_reorder_create_approve_send_chain_reserves_order_once`, `test_chat_po_creation_cannot_bypass_per_order_money_limit`, `test_quote_po_creation_uses_chat_money_checks` |
| Fake model “order 10 of X” yields confirmable proposal | `test_fake_order_ten_produces_confirmable_reorder` |
| Permission filtering, no model approval, synthetic evals through real use case | `test_new_order_tool_requires_lookup_permission_and_campaign_second_person`, `test_new_copilot_eval_cases_with_fake_gateway`, expanded existing AI safety inventories |
| Delay respected by claim/job; zero; manual no delay; origin governs human/policy decisions | `test_not_before_respected_by_direct_claim_and_scheduled_job`, `test_staff_direct_mail_no_delay_and_ai_origin_uses_delay_even_with_human_decider`, `test_activity_projects_delivery_and_policy_mail_gets_delay` |
| Stop vs claim, already sending, never sends, rollback lock contention | `test_stop_vs_claim_race_exactly_one_outcome` (five threaded races), `test_stop_after_claim_sending_fails`, `test_stop_never_sends_and_audits_delivery_stopped`, `test_claim_waits_for_stop_transaction_that_rolls_back` |
| Stop permission/tenant scope | `test_stop_authorization_action_approver_and_cross_business` |
| Delivery mappings, executed vs scheduled/stopped, Activity | `test_delivery_state_mapping` (six cases), detail/card assertions and policy Activity test |
| Work/exception contents, due date, pause, seven days, ordering/pages, RLS | `test_work_queue_and_exceptions_contents_pagination_and_rls`, `test_a1_new_tables_have_forced_rls_and_decision_provenance_is_immutable` |
| Business Reply-To and text/HTML without real mail | `test_po_mail_reply_to_and_rendering_without_external_send` (memory email adapter) |
| Populated migration preserves legacy human/outbox/supplier rows; downgrade protection | `test_a1_upgrade_preserves_legacy_supplier_outbox_and_human_decision`, `test_a1_downgrade_refuses_chat_provenance_loss`, `test_a1_downgrade_refuses_stopped_delivery_loss` |

Independent read-only Tier A review found three issues, all fixed with regression coverage: stale cached proposal
state after waiting on a lock; a delivery job lost when Stop rolled back after lock contention; and missing result
references preventing subsequent copilot steps. No further high-severity findings. Reviewer did not run DB tests.

## Validation

- Initial red test run, before registering send_po: **5 failed**, as expected for the missing executor.
- Initial chat boundary suite: **14 passed, 1 failed** due to a test fixture using the wrong role table name;
  corrected to existing `role_grants`, then strengthened with a real HTTP 403 after changing owner to operator.
- Early combined architecture run: **68 passed, 2 failed** (cross-module tests were in module directories;
  moved them to integration without weakening boundaries; OpenAPI was intentionally not yet regenerated).
- Focused A1 suite after integration/review fixes: **47 passed in 20.73 s**.
- Broad requested modules/integration run, excluding only the SMTP file: **1,103 passed, 3 failed in 729.20 s**.
  Failures were two old tool inventories missing propose_send_po and its 303-character description exceeding
  the existing 300-character contract. Expanded the inventories/cross-business attack coverage and shortened
  the description; no assertion was removed or loosened.
- Final affected regression run (all A1 tests then present, proposal/read tools, structural safety, copilot and
  architecture): **163 passed in 110.75 s**. This covers all three broad-run failures, the copilot module split,
  permissions, threaded races, populated migration upgrade/downgrade guards and current OpenAPI.
- Final A1 run after adding supplier revocation, both quote-creation money cases and reservation RLS assertions:
  **55 passed in 22.08 s**. Counts from overlapping runs are not summed as distinct tests.
- Ruff: **All checks passed!** Format: **534 files already formatted**. mypy:
  **Success: no issues found in 296 source files**. Final post-document checks are recorded below if different.
- Backend OpenAPI regenerated with `.venv/bin/python -m app.cli openapi > openapi.json` (the command prints
  JSON; redirect is necessary to update the file). Snapshot/exposure and module/route architecture checks passed.
- Final post-generation OpenAPI/module-import/route-permission check: **11 passed in 2.71 s**.
- `git diff --check`: passed. Native runtime and compiled extension audit: arm64.
- `make check`: **not run**. Inspected with `make -n check`: the real target starts Docker, frontend tooling,
  deploy checks and uv-managed commands; these exceed this run's no-Docker/no-installs/backend-only restrictions.
- **Initial handoff: 35 SMTP tests not run** (`notifications/tests/test_smtp.py`, collection-only count; superseded by the follow-up below). That file includes fake
  loopback SMTP/TLS servers and conflicts with the explicit DB-only network limit. Clarification was requested;
  no answer/permission has been received. The new PO Reply-To test uses an in-memory email adapter without network.
- The entire broad command was not repeated after the fixes; its three failed cases and affected safety surfaces
  passed in the 163-test rerun. No known test failure remains in the executed checks. This is not a claim that
  the original unmodified full command or make check passed.

Unverified at the initial handoff: those 35 SMTP tests (now resolved by the follow-up below), make check, live provider evals, real supplier/inbox delivery,
deployment, frontend/TS generation and browser UI. Live evals and TS regeneration belong to the supervisor.
The full P10-A1 phase remains in progress for integration. No frontend file changed; no dependency, Docker,
external service call, cloud resource, commit, push or publication. Backend changes stay uncommitted for review.

## Deviations and rationale

- Added `Supplier.active` because no active/inactive representation existed; a mere existence check could not
  enforce the requested active-supplier rule. Default true preserves existing supplier records.
- Added whitelisted result references and fenced status/result context to make the multi-turn ordering chain
  usable without asking staff to locate internal IDs. No automatic execution/cascade is introduced.
- Kept A0's one-action rule allowlist and evaluation-only Activity scope. Only its delivery field is extended;
  no new autonomous send delegation is enabled. Work queue and proposal/chat detail cover the new actions.
- Rewrote the purchasing module README into a ≤80-line module card, retaining its public surface, security,
  pricing and lifecycle rules and linking the full specification. No ADR changed.
- Money exposure uses the existing purchasing `total_minor` and priced draft/quote snapshots. This model has no
  separate tax/shipping fields; A1 does not add charge accounting. Separate tax/shipping inclusion scenarios are
  not representable or tested here; charges must already be part of the recorded PO total for these caps to cover them.
- Verification exceptions are the DB-only network restriction and make check's Docker/frontend prerequisites
  above. They are not equivalent evidence for the unrun original checks.

## Files changed

Working-tree inventory: 47 modified files and 22 new files; all task-authored (initial tree was clean).

Modified:

```text
EVENT_WORK.md
backend/app/modules/ai/README.md
backend/app/modules/ai/api.py
backend/app/modules/ai/tests/test_copilot.py
backend/app/modules/ai/tests/test_eval_harness.py
backend/app/modules/ai/tests/test_propose_tools.py
backend/app/modules/ai/tests/test_safety_pass_boundaries.py
backend/app/modules/ai/tests/test_safety_pass_structural.py
backend/app/modules/ai/tools/registry.py
backend/app/modules/ai/usecases/copilot.py
backend/app/modules/approvals/README.md
backend/app/modules/approvals/api.py
backend/app/modules/approvals/autonomy_schemas.py
backend/app/modules/approvals/autonomy_service.py
backend/app/modules/approvals/decisions.py
backend/app/modules/approvals/engine.py
backend/app/modules/approvals/models.py
backend/app/modules/approvals/public.py
backend/app/modules/approvals/reading.py
backend/app/modules/approvals/schemas.py
backend/app/modules/approvals/service.py
backend/app/modules/marketing/executors.py
backend/app/modules/notifications/README.md
backend/app/modules/notifications/api.py
backend/app/modules/notifications/models.py
backend/app/modules/notifications/public.py
backend/app/modules/notifications/registration.py
backend/app/modules/notifications/schemas.py
backend/app/modules/notifications/service.py
backend/app/modules/notifications/templates.py
backend/app/modules/org/README.md
backend/app/modules/org/schemas.py
backend/app/modules/purchasing/README.md
backend/app/modules/purchasing/executors.py
backend/app/modules/purchasing/models.py
backend/app/modules/purchasing/public.py
backend/app/modules/purchasing/quote_executors.py
backend/app/modules/purchasing/registration.py
backend/app/modules/purchasing/schemas.py
backend/app/modules/purchasing/service.py
backend/app/modules/purchasing/service_suppliers.py
backend/app/platform/registry.py
backend/openapi.json
docs/roadmap/07-workflow-engine.md
docs/roadmap/ai/contracts.md
docs/roadmap/phases/STATUS.md
docs/runbooks/email.md
```

New:

```text
backend/app/modules/ai/prompts/copilot.v2.md
backend/app/modules/ai/tools/orders.py
backend/app/modules/ai/usecases/copilot_cards.py
backend/app/modules/ai/usecases/copilot_threads.py
backend/app/modules/ai/work_queue_api.py
backend/app/modules/approvals/chat.py
backend/app/modules/approvals/queue.py
backend/app/modules/notifications/outbound.py
backend/app/modules/purchasing/delivery.py
backend/app/modules/purchasing/planning.py
backend/app/modules/purchasing/work_queue.py
backend/evals/cases/copilot/order-cables.yaml
backend/evals/cases/copilot/order-quantity-mismatch.yaml
backend/evals/cases/copilot/typed-yes.yaml
backend/migrations/versions/20261105_0900_chat_delivery.py
backend/tests/integration/test_a1_chat_confirm.py
backend/tests/integration/test_a1_copilot_orders.py
backend/tests/integration/test_a1_migration.py
backend/tests/integration/test_a1_po_delivery.py
backend/tests/integration/test_a1_send_delay.py
backend/tests/integration/test_a1_work_queue.py
docs/roadmap/ai/cards/P10-A1-backend.md
```


## Supervisor SMTP follow-up — 2026-11-05

The supervisor reported three failures from full make check: multipart/stable-ID and STARTTLS/implicit-TLS
second deliveries returned skipped. With localhost networking now explicitly permitted, all three were
independently reproduced (**3 failed in 2.07 s**).

Root cause: stale test setup. Each test's initial public enqueue was claimable and the SMTP attempt returned
retry (451 response or rejected unknown CA). The service then correctly scheduled next_attempt_at one minute
later. Tests immediately called deliver again; A1's under-lock retry-time guard correctly refused that early
claim. These are manual messages with not_before=null; no AI send delay was applied. Public enqueue explicitly
sets next_attempt_at from the application clock, so these failures are not app-clock/database-now mismatches.
No production service, clock, model, migration, adapter or claim condition changed in this follow-up.

Changed test_smtp.py to freeze the app clock, inspect persisted pending/attempts/next_attempt_at and null
not_before, assert a claim one microsecond before due is skipped with no attempt/status/timing mutation or
additional SMTP DATA, then advance to the exact persisted due time. Original multipart/header/HTML escaping,
stable Message-ID, rejected untrusted certificate, hostname verification, CERT_REQUIRED and real verified
STARTTLS/implicit-TLS delivery assertions remain. Fixed targeted cases: **3 passed in 2.04 s**.

Requested exact notifications + integration suite (same smb_a0 application/owner URLs, no exclusions):
`cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider app/modules/notifications tests/integration`
→ **261 passed in 147.11 s**, including all 35 SMTP cases. The earlier SMTP-unrun limitation is resolved.
Ruff check/format: **All checks passed; 534 files already formatted**. mypy: **no issues in 296 source files**.
`git diff --check` passed. Full make check was not rerun here (Docker/frontend remain outside this follow-up);
no claim is made about the supervisor's subsequent full run. Native arm64 Python verified.
Follow-up files: notifications/tests/test_smtp.py, notifications/README.md, this card, EVENT_WORK.md and the
P10-A1 verification note in STATUS.md. Existing supervisor/frontend work is preserved. No external hosts,
Docker, installation, credentials, commit or deployment used.


## Supervisor live-eval follow-up — staff quantities and needs_human (2026-11-05)

Source: supervisor-run [mimo copilot report](../../../../backend/evals/reports/20261105T163147234874Z-copilot-vultr-mimo-v2.6-flash.md).
Observed report: 23 cases, 78.26% pass; new quantity-mismatch case proposed 5 when staff explicitly ordered 10;
order-cables and several old fact cases overused needs_human. The supplier case also missed check_stock and answer
facts; this correction does not claim those other live-model behaviors pass without the supervisor's rerun.

Contract written first in the A1 live-eval correction section. This is a product correction with no migration,
new dependency, provider, frontend change, deployment or external call.

### Behavior and decisions

- `propose_reorder` and `propose_send_po` accept requested_quantities[{item_id,quantity}], positive ≤10,000,
  max three decimal places. Existing item/business/product/stock-active checks and open-intent guards remain.
  Reorders move into reorders.py; staff_quantities.py owns numeric copying/provenance, keeping each file small.
- Requested quantities are grounded in a persisted copilot AI run, its exact triggering user message and owned
  thread/requester, not just actor_kind or an arbitrary on-behalf-of id. Name-before/after-number, ×/x, basic
  number words and explicit record-bound single-item expressions are recognized. Model omission cannot turn
  “Order 10 Cable set” or “Cable set ×10” into suggestion 5. A conflicting argument, unknown item, another
  item's number or ambiguous/corrected/negated quantity is refused. Stock facts are not ordering instructions.
- Tool results and previews say Quantity requested by <authenticated staff> and show the separate suggestion.
  Existing reason_text retains this provenance in reorder drafts; default/background explanations remain intact.
  No need to ask for permission again when an unambiguous staff quantity differs from the suggestion.
- PO creation may override source-draft quantities through requested_lines in the immutable action payload.
  Existing conversion snapshot/version checks freeze actual quantities, prices and total. Existing POs are not
  silently changed on approval/send; conflicting quantities produce an explicit blocker. Revisions to existing
  draft POs remain the ordinary staff workflow. No automatic multi-executor cascade is introduced.
- New requested_lines is omitted from serialization when absent, preserving hashes of old unchanged pending
  proposals. No manual rewrite of immutable revisions or fabricated provenance is needed.
- Chat caps use the requested quantity's actual snapshot cost. Tests demonstrate 10×6,000 cannot bypass the
  50,000 per-order gate and three 10×4,000 creates cannot bypass the 100,000 daily gate (80,000 reserved after two).
- ExecutorSpec.autonomy_reasons is an optional executor-owned callback. Inventory compares actual payload
  quantities to current suggestions before shadow/live cap reservation; a mismatch remains pending with
  fell_back/quantity_not_suggested. Quantity provenance text is not trusted for this decision. U4/background
  tools cannot enter the staff quantity path. A0 policy execution retains its locked strict quantity recheck.
- `propose_send_po` now requires inventory.stock.read alongside purchasing.po.read, since it reads suggestion data;
  both catalogue filtering and direct invocation enforce that gate. No new permission is added.
- Prompt copilot.v3 retains v1/v2 and distinguishes ordinary requester confirmation from real blockers.
  FinalStep.needs_human's JSON schema description matches. Copilot proposal results now carry fresh-identity
  confirmation.can_confirm/reason, so an owner and an operator get different, accurate confirmation hints even
  though the AI contexts deliberately strip approve permissions. No postprocessing forces model flags false.
- Mismatch eval now requires one reorder proposal with actual quantity 10 and needs_human=false; order-cables
  additionally checks actual 5. New order-suggested omits a quantity and checks computed 5; 24 copilot cases now.
  Old fact cases and previous live reports are preserved. Supervisor reruns the live provider; no success claim here.

### Verification and review

Initial new-quantity red tests: **7 failed, 5 passed** before implementation. First affected run: **129 passed**.
Review drove extra boundary coverage: wrong item/stock-read gate, ×/x omissions, legacy hashes, current requester
permissions, stock facts, ambiguity/corrections and actual money exposure. Intermediate format regressions were
fixed without removing checks. Final preflight: **41 passed in 13.73 s**, including all five input-budget stages
and the staff quantity boundary tests. Earlier focused run: **64 passed in 30.29 s** (overlapping suites).

First broad run was intentionally interrupted after identifying a fixture coupled to the growing production
copilot prompt: **1 failed, 487 passed**. Its 3,000-token trimming test now uses the existing test_agent.v1 prompt;
token limit and every original retention/repair/failure assertion remain unchanged. No production budget changed.
The final requested offline command, with the supplied smb_a0 application/owner URLs and no exclusions:
`cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider app/modules/ai app/modules/approvals app/modules/inventory app/modules/purchasing tests/integration`
→ **1,110 passed in 780.62 s (13:00)**. This includes the fake-model copilot and offline eval-harness tests,
both staff-quantity integration files, and existing approvals/inventory/purchasing/integration regressions.
Architecture checks: **17 passed in 4.01 s**. Ruff check/format: **All checks passed; 542 files already formatted**.
mypy: **no issues in 298 source files**. Backend OpenAPI regenerated; diff whitespace checks passed.
Native arm64 Python verified. Live-provider accuracy after this correction remains untested here.

Independent read-only review found cross-item attribution, a stock-read permission gap, legacy serialization
changes, missing confirmation eligibility and corrected/negated quantity handling. All were fixed and have
regression tests. The reviewer did not run the database tests.

Follow-up files: ai/agent/protocol.py; prompts/copilot.v3.md; tools/{proposals,proposing,registry,orders,reorders,
staff_quantities}.py; usecases/copilot.py; tests/{test_batch_c_completion,test_eval_harness}.py; approvals/autonomy.py;
platform/registry.py; inventory/executors.py; purchasing/{schemas,executors,public}.py; module cards;
evals/cases/copilot/{order-cables,order-quantity-mismatch,order-suggested}.yaml; integration tests
{test_a1_copilot_orders,test_a1_staff_quantities,test_a1_staff_quantity_boundaries}.py; contracts, STATUS and EVENT_WORK.
Backend OpenAPI regenerated. make check and live eval remain supervisor-owned; no Docker, installation or commit.

## Second live-eval follow-up — suggestion recovery and advice flags (2026-11-05)

Source: supervisor-run [second mimo copilot report](../../../../backend/evals/reports/20261105T172821648815Z-copilot-vultr-mimo-v2.6-flash.md).
Observed report: 24 cases, 79.17% pass; order-cables and quantity-mismatch pass. The no-quantity order case
failed after ai.quantity_not_requested; four stock/supplier answers overused needs_human. The supplier case
also missed answer facts. No live-provider run was made here, so those accuracy results remain supervisor observations.

Contract updated first. When staff did not request a quantity, propose_reorder now accepts a model argument
equal to the current computed suggestion as Suggested N, with requested_by=null. It never manufactures staff
provenance. A different number is refused with ai.quantity_not_requested and an observation explicitly saying
to call propose_reorder again without requested_quantities/quantity to use the suggested N. The message puts
the retry instruction and quantity before the item name so bounded tool-error truncation retains the recovery.
No-suggestion cases ask for a staff quantity; existing PO conversion semantics are unchanged.

Explicit staff quantities remain authoritative, including “I need N item”. Unresolved item-attached numbers
fail closed instead of being called quantity-free. Recognized stock facts remain separate. Independent review
caught the need-verb substitution edge and its paired negation guard; both were corrected and tested, including
omitted arguments, model 5/10, and “I do not need 10 …”. Background runs still cannot pass explicit quantities,
even when an argument equals the suggestion. Existing A0 policy/autonomy checks and chat money caps remain.

Edited the unshipped copilot.v3 in place, preserving v1/v2. It says pass quantity only for a number stated by
the user, retry a recoverable quantity error, and reserve needs_human for unresolved blockers. Advice and warnings
belong in the answer text; complete stock/supplier answers use false even when recommending action. Two short
grounded stock/supplier examples and FinalStep's JSON schema describe that same meaning. No flag rewriting.
Existing eval case expectations stay intact. The no-quantity fake-model eval now also deliberately supplies
the computed 5 to exercise the live failure's recovery-compatible path.

Verification: initial new checks **3 failed, 1 passed**; first focused run **40 passed**. Review edge red run
**4 failed, 1 passed**; focused AI/order/quantity integration run **54 passed in 25.34 s** before the paired
need-negation additions. Broad runs were deliberately interrupted for those review fixes, without skipping
or weakening assertions. Final exact requested command, with the supplied smb_a0 URLs and no exclusions:
`cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider app/modules/ai app/modules/approvals app/modules/inventory app/modules/purchasing tests/integration`
→ **1,120 passed in 758.64 s (12:38)**, including fake-model copilot, offline eval harness and the paired
need-negation additions. Architecture: **17 passed in 4.26 s**. Ruff check/format: **All checks passed;
543 files already formatted**. mypy: **no issues in 298 source files**. Backend OpenAPI regenerated;
`git diff --check` passed. Native arm64 Python verified. Focused read-only reviewer found no further
material issue after the paired guard correction; the reviewer did not run DB/network tests.

Files: ai/tools/staff_quantities.py, prompts/copilot.v3.md, agent/protocol.py, ai/README.md;
integration test_a1_staff_quantities.py, test_a1_staff_quantity_boundaries.py, test_a1_copilot_orders.py;
contracts.md, this backend card, STATUS.md and EVENT_WORK.md. Backend OpenAPI regenerated.
No new migration, dependency, frontend edit, external call, commit or deployment. Full make check is dry-run
only because it starts Docker and includes out-of-scope frontend/deploy checks; the supervisor runs that gate
and the live eval. No claim is made that the remaining live-model answer-fact failures are fixed.
