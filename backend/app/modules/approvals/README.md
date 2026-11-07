# approvals (M08) — module card

Spec: [docs/roadmap/modules/M08-approvals.md](../../../../docs/roadmap/modules/M08-approvals.md) · Engine design: [07](../../../../docs/roadmap/07-workflow-engine.md)

- Tables: `action_proposals`, `proposal_revisions` (append-only), `approval_decisions` (append-only), `execution_attempts`.
- Public: `create_proposal`, `cancel_proposal`, `count_pending`, `find_open_proposals`, `OpenProposal`, `Evidence`, executor types (`ExecutorSpec`, `PreviewLine`, `ExecutionMeta`), events.
- Executors are registered by owning modules: `reg.add_executor(ExecutorSpec(...))`.
- Jobs: `approvals.execute`, `approvals.expire_scan` (cron 60 s), `approvals.expire_business`.
- Events: `approvals.proposal_created`, `approvals.proposal_decided`, `approvals.proposal_closed`.
- Dev/test executor: `testing.noop_write` (`APP_ENV=test` or `DEV_TEST_EXECUTORS=1`).

`find_open_proposals(db, ctx, *, conversation_id=None, action_types=None, item_id=None, invoice_id=None, include_executing=False)` requires `approvals.proposal.read` or
`ai.assistant.use`. It returns this business's `pending_review` and `approved` proposals (also `executing` when requested), newest first, as DTOs with
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

P10-A0: [Autonomy contract](../../../../docs/roadmap/ai/contracts.md#autonomy-rules-p10-a0).
- New tables: `autonomy_rules`, `autonomy_evaluations` (append-only), `autonomy_counters`; forced business RLS.
- Rule-eligible actions: `inventory.create_reorder_draft` and C1 `conversations.send_reply`. AI policy decisions;
  staff proposals always retain the human path. `decided_by_kind=policy` has null `decided_by`, rule id/version and
  the real grantor; execution resolves that grantor's current permissions and re-checks rule version + pause.
- Settings/rule share locks protect execution against revocation; cap reservation uses a conditional daily upsert.
  Shadow successes reserve cap too. New rules always start shadow; a versioned update can enable acting.
- Policy audit actors are system with the grantor as on-behalf-of, plus a rule/version marker on every audit entry.
- Public rule API: `RulePut`, `RuleOut`, `RulesOut`, `PauseOut`, `ActivityPage`,
  `list_autonomy_rules(db, ctx)`, `put_autonomy_rule(db, ctx, RulePut)`, `autonomy_rule_out(db, ctx, rule)`,
  `autonomy_activity(db, ctx, *, limit=50, cursor=None)`, `check_ai_proposal_allowed(db, ctx, action_type)`.
- HTTP belongs to AI (`/api/v1/ai/autonomy`): GET/PUT rules, POST pause/resume (`org.settings.write`),
  GET activity (`approvals.proposal.read`). Optimistic version 0 creates; stale versions return 409.
- `decisions.py` shares immutable decision/audit/event/job writing; `reading.py` owns proposal queries.

- P10-A0 part 2: public `proposal_notification_state(db, ctx, proposal_id)` supplies scoped lifecycle/delegation
  fields to event consumers without importing approvals internals. Policy detail includes the grantor display name.
  Activity includes action type and version-correct grantor identity/name. `autonomy_history.py` resolves historical
  versions through rule-save audit snapshots, with legacy-summary support; missing history stays unavailable.

P10-A1: `ExecutorSpec.second_person` is a bool or payload callback (default false); `chat_money`
returns total/currency/domain subject. Typed `decided_via=approvals|chat` preserves human provenance.
`chat.py` owns explicit requester approvals, fresh approve permission, exact revision/hash and
second-person checks, revalidates previews and atomically reserves business-local daily money.
`chat_confirm_counters` and `chat_confirm_reservations` use forced RLS; source draft/quote/PO
subjects count once per day with top-ups and no refunds. Normal decisions retain allow_self_approval;
requesters cannot self-approve second-person actions. Engine rechecks second-person for chat/self.
Public `chat_origin_run_id`, `chat_card`, `confirm_chat` serve AI's owned-thread path; no model tool.
`outbound_authority` returns scoped origin/action and immutable source-message id. Detail and Activity get independent
`delivery_state` from notifications/conversations' registered readers, preserving the dependency graph. Activity still
lists autonomy evaluations. `proposal_queue_items`/`fallback_queue_items` serve the read-only work views.

A1 live-eval correction: `ExecutorSpec.autonomy_reasons` supplies payload-owned conditions. Reorder
quantities differing from current suggestions record fell_back/quantity_not_suggested before any
shadow/live cap reservation. Human/chat decisions may use requested quantities; policy execution
retains its strict locked recheck. No rule allowlist expansion or model-derived policy judgment.

P10-C1: public `invalidate_ai_replies(db,ctx,conversation_id,generation)` cancels open assistant reply proposals
from older ownership generations, records failure_code conversations.ai_paused and an audit/event. Staff proposals
are preserved. Row locks use SKIP LOCKED to avoid inversion with conversation takeover; the reply executor checks
ownership again and durable hand-over/ownership subscribers clean up skipped proposals.

P10-B1: proposal causality/producer fields; registered AI execution guards and atomic chain action accounting.
Disabled producer policy actions return to a fresh human-review revision; immutable decisions remain. Public
`team_summary`, `team_activity`, `team_return_items`, `workflow_for_result` supply scoped metadata and durable lineage; Team activity includes the owning proposal's recorded depth (P10-gaps).

P10-G privacy: `public_auto_replies` registers the public-profile boolean callback for an active, non-shadow act_notify/act send_reply rule while autonomy is unpaused.
