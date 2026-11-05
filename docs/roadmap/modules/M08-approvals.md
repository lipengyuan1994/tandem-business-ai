# M08 · approvals

| Field | Value |
|---|---|
| Release / phase | MVP · P2 (engine) / P3 (UI polish) |
| Tier / kind | **A** · PRE (a generic staff change-request engine, useful without AI) |
| Depends on | platform, org, identity (org: `allow_self_approval` setting) |
| Owns tables | `action_proposals`, `proposal_revisions`, `approval_decisions`, `execution_attempts`, `autonomy_rules`, `autonomy_evaluations`, `autonomy_counters` |

## Purpose
A generic engine for "propose → decide → execute" with exact payload binding. It knows nothing about bookings or stock. Business modules register executors (through the platform registry), and the engine looks them up by `action_type`. The full behaviour is specified in [07-workflow-engine.md](../07-workflow-engine.md#proposals). This file lists the contract.

## Tables
Columns as listed in [07](../07-workflow-engine.md#tables-owned-by-m08-approvals). Additional constraints:
- `action_proposals.status` check: `pending_review`, `approved`, `rejected`, `expired`, `cancelled`, `executing`, `succeeded`, `failed`.
- `proposal_revisions`: unique `(proposal_id, revision_no)`. INSERT/SELECT only for the app role.
- `approval_decisions`: INSERT/SELECT only; `decision` check `approve` \| `reject`.
- `execution_attempts.idempotency_key` unique.
- Index `action_proposals (business_id, status, created_at desc)`.

## Business rules
1. Creating a proposal validates the payload against the executor's model and runs `validate()`. `preview` comes from `describe()`.
2. Editing (`POST /proposals/{id}/revisions`) is allowed only in `pending_review`. It creates revision n+1, recomputes the hash and preview, and points `current_revision_id` to it. Only staff with the approve permission for that action type, or the creator, may edit.
3. A decision must name the current `revision_id` and its `payload_hash`. A mismatch returns 409.
4. Approver authority: holds `executor.approve_permission` at decision time. Self-approval follows the business setting,
   including assistant proposals whose `origin_user_id` is the deciding user. Requester read/revision access remains.
   Authority is re-checked at execution time.
5. Execution follows the TX-1 / TX-2 / TX-3 algorithm in [07](../07-workflow-engine.md#executing-job-approvalsexecute). Human decisions execute as the approving user with permissions re-resolved now. P10-A0 policy decisions execute as system with current grantor permissions, on-behalf-of grantor and a rule/version audit marker. Both set `proposal_id`.
6. Expiry job: `pending_review` past `expires_at` → `expired` + `proposal_closed` event.
7. `cancelled`: the creator (or an admin) may cancel a pending proposal. The assistant cancels its own proposals when it supersedes them.
8. A proposal's evidence and preview never include data from another business (they are built in the same context).

## Public interface (`public.py`)
```python
def create_proposal(db, ctx, *, action_type: str, payload: dict, title: str, evidence: list[Evidence],
                    expires_in: timedelta | None = None, conversation_id: UUID | None = None) -> ProposalDto
def cancel_proposal(db, ctx, proposal_id, reason) -> None
def get_proposal(db, ctx, proposal_id) -> ProposalDetailDto
def count_pending(db, ctx, *, for_user_permissions: frozenset[str] | None = None) -> int
def find_open_proposals(db, ctx, *, conversation_id: UUID | None = None,
                        action_types: list[str] | None = None) -> list[OpenProposal]
def proposal_exists(db, ctx, proposal_id: UUID) -> bool  # scoped reference validation; caller authorizes its operation
# types used by other modules to register executors:
from app.platform.registry import ExecutorSpec, PreviewLine, ExecutionMeta   (re-exported)
```

`find_open_proposals` requires `approvals.proposal.read` or `ai.assistant.use`, returns only the current business's
`pending_review` or `approved` proposals, newest first, and combines the optional filters. An empty `action_types`
list returns no rows. `OpenProposal` contains `id`, `action_type`, `title`, `origin_kind`, and `created_at`.

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/approvals/proposals` | `approvals.proposal.read` | `?status=pending_review&action_type=&mine=true&can_approve=true` |
| GET | `/approvals/proposals/{id}` | read | Current revision, preview, evidence, history (revisions, decisions, attempts), delivery status of related outbox messages |
| POST | `/approvals/proposals` | `approvals.proposal.create` | Staff-originated proposal (any registered action type) |
| POST | `/approvals/proposals/{id}/revisions` | approve permission of the type, or creator | `{payload, revision_id_base}` |
| POST | `/approvals/proposals/{id}/decisions` | approve permission of the type | `{decision, revision_id, payload_hash, comment}` |
| POST | `/approvals/proposals/{id}/cancel` | creator or admin | |
| GET | `/approvals/action-types` | read | Registered types with payload JSON schema (drives the edit forms) |

## Permissions
`approvals.proposal.read`, `approvals.proposal.create`. Approve permissions are defined by the owning modules (`<module>.proposal.approve`).

## Events
Published: `approvals.proposal_created`, `approvals.proposal_decided`, `approvals.proposal_closed` (outcome: `succeeded` \| `failed` \| `rejected` \| `expired` \| `cancelled`).

## Jobs
`approvals.execute` (per approval); `approvals.expire_due` (cron, every minute).

## Error codes
`approvals.unknown_action_type`, `approvals.invalid_payload`, `approvals.not_pending`, `approvals.revision_changed`, `approvals.expired`, `approvals.not_authorized_to_approve`, `approvals.self_approval_forbidden`, `approvals.approver_no_longer_authorized` (execution failure).

## UI
`/approvals`: queue with tabs *Needs my decision*, *All pending*, *Recent outcomes*. Detail page: title, "AI proposed" badge, **preview diff**, evidence list (links to message, slot search, stock check), revision history, an edit form generated from the payload schema with per-action-type custom forms for booking and reply, and an approve button labelled with the effect. After approval it shows live execution status and delivery status.

## Tests (must exist)
- `test_create_validates_payload_and_builds_preview`
- `test_edit_creates_revision_and_old_hash_cannot_be_approved`
- `test_self_approval_blocked_unless_setting_enabled`
- `test_double_approve_click_executes_once` (concurrent requests)
- `test_two_workers_cannot_both_execute` (concurrent jobs)
- `test_execution_fails_cleanly_when_approver_role_revoked_before_run`
- `test_execution_business_error_rolls_back_all_local_effects` (fake executor that writes then raises)
- `test_crash_mid_execution_then_retry_succeeds_once` (simulate exception after writes before commit)
- `test_expiry_job_expires_and_publishes_closed`
- `test_cross_business_proposal_not_visible`

## As built (P2, 2026-11-03)
- The engine re-checks the proposal status **under the row lock** before running an executor. A concurrency test caught several workers executing the same proposal via the crash-resume path without this check.
- Expiry: the cron job `approvals.expire_scan` finds due businesses through the security-definer function `businesses_with_due_proposals(now)`, then runs one `approvals.expire_business` job per business inside its tenant.
- `testing.noop_write` is a harmless executor registered only when `APP_ENV=test`, or in dev with `DEV_TEST_EXECUTORS=1`. It lets the approvals UI be tried before business modules exist.
- `ProposalOut.can_decide` tells the UI whether the current user may decide.

## Out of scope
Multi-step approval chains (two approvers) — v1 option "second approver required for action types X". Scheduled execution ("approve now, run at 9:00") — later.

P10-A0 extends this contract with delegated policy decisions for AI reorder drafts only; see
[Autonomy rules](../ai/contracts.md#autonomy-rules-p10-a0) for tables, API, locks, levels, cap/shadow semantics and errors.
