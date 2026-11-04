# approvals (M08) — module card

Spec: [docs/roadmap/modules/M08-approvals.md](../../../../docs/roadmap/modules/M08-approvals.md) · Engine design: [07](../../../../docs/roadmap/07-workflow-engine.md)

- Tables: `action_proposals`, `proposal_revisions` (append-only), `approval_decisions` (append-only), `execution_attempts`.
- Public: `create_proposal`, `cancel_proposal`, `count_pending`, `find_open_proposals`, `OpenProposal`, `Evidence`, executor types (`ExecutorSpec`, `PreviewLine`, `ExecutionMeta`), events.
- Executors are registered by owning modules: `reg.add_executor(ExecutorSpec(...))`.
- Jobs: `approvals.execute`, `approvals.expire_scan` (cron 60 s), `approvals.expire_business`.
- Events: `approvals.proposal_created`, `approvals.proposal_decided`, `approvals.proposal_closed`.
- Dev/test executor: `testing.noop_write` (`APP_ENV=test` or `DEV_TEST_EXECUTORS=1`).

`find_open_proposals(db, ctx, *, conversation_id=None, action_types=None, item_id=None, invoice_id=None)` requires `approvals.proposal.read` or
`ai.assistant.use`. It returns this business's `pending_review` and `approved` proposals, newest first, as DTOs with
`id`, `action_type`, `title`, `origin_kind`, `created_at`. Filters combine; `None` leaves a filter unrestricted and
an empty action-type list returns no rows. `item_id` matches the string UUID in a `lines` array element of the
proposal's current revision payload using JSONB containment; historical revisions do not match.
`invoice_id` matches the top-level string UUID in that current payload with JSONB containment. It combines with the
item, conversation and action filters; nested or historical invoice references do not match.

`proposal_exists(db, ctx, proposal_id)` is a status-independent reference lookup scoped to `ctx.business_id`.
It returns only a boolean, without exposing proposal content; the calling module authorizes its own operation.
Scheduling uses it before binding a hold, so missing and foreign-business proposal ids cannot be bound.

Self-approval is blocked for both staff proposals and assistant proposals made on the deciding user's behalf, unless
the business enables `allow_self_approval`. Requesters retain their existing read and revision access.
