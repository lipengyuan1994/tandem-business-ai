# PR-02 · Platform prerequisites for safe AI orchestration

Kind **PRE** (platform and module features, no AI) · Tier A · Size M · Depends on: — · Blocks: AI-01 (job/cron forms), AI-02 (local client), AI-07 (hold binding), AI-08 (inputs, guard), AI-10 (self-approval)

## Goal
Seven small, general platform features that the AI layer needs and that the existing code does not offer. Each is
useful on its own and fully testable without AI. A consistency review of the AI plan found them missing (2026-11-03).

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/integration-map.md` §A, §B "Inputs and guards", §F (the exact signatures to provide).
2. `backend/app/platform/jobs.py` (whole file), `backend/app/platform/registry.py` (`add_job`, `add_cron`, `CronSpec`).
3. `backend/app/modules/scheduling/service.py` functions `job_release_holds_scan`, `place_hold`, `on_proposal_closed`
   and the migration defining `businesses_with_expired_holds` (`backend/migrations/versions/20261025_1640_scheduling_core.py`).
4. `backend/app/modules/approvals/service.py` lines 80–110 and 195–225 (`_can_create`, `create_proposal`, self-approval check).
5. `backend/app/platform/http.py` (whole file).
6. `backend/app/modules/conversations/public.py`, `schemas.py` (`ThreadDto`, `MessageOut`).
7. `docs/roadmap/04-repository-and-conventions.md` "Code patterns".

## You build
1. **Non-transactional jobs.** `Registry.add_job(kind, handler, *, transactional: bool = True)`; store the flag. In
   `run_job`, for `transactional=False` call `handler(ctx, payload)` with **no open transaction**, then mark the job
   succeeded in its own short transaction; exceptions use the existing `_fail` path. Existing jobs unchanged.
2. **Per-business crons.** `Registry.add_cron(name, job_kind, every_seconds, *, per_business: bool = False)`. In
   `enqueue_cron`, a per-business spec enqueues one job per active business (`business_id` set, dedupe key
   `cron:{name}:{slot}:{business_id}`), using a new SECURITY DEFINER function `active_business_ids()` (status `active`;
   `search_path = pg_catalog, public, pg_temp`; revoke from public, grant to smb_app) — follow the
   `businesses_with_expired_holds` pattern and the hardening rules from 2026-11-03.
3. **Self-approval for assistant proposals.** The self-approval rule also applies when `origin_kind == "assistant"` and
   `origin_user_id == ctx.user_id` (a proposal created on behalf of a user). Also review `approvals/service.py:166`
   (the read rule using `origin_user_id`) and keep it as is for assistant proposals (the requester may read it).
4. **Hold binding.** `scheduling.public.bind_hold(db, ctx, hold_id, proposal_id) -> None`: same business, appointment
   status `held`, `proposal_id` null or already this proposal (idempotent); else `Conflict("scheduling.hold_not_bindable")`.
   After binding, the existing `on_proposal_closed` releases the hold when the proposal does not succeed.
5. **Message context.** `conversations.public.get_message_context(db, ctx, message_id) -> MessageContext` with the fields
   in integration-map §B (permission `conversations.thread.read`; another business's id → NotFound).
6. **Open proposals lookup.** `approvals.public.find_open_proposals(db, ctx, *, conversation_id=None, action_types=None)
   -> list[OpenProposal]` (status `pending_review` or `approved`; permission `approvals.proposal.read` or
   `ai.assistant.use`).
7. **Local inference transport.** `app.platform.http.client(local: bool = False)`: with `local=True` allow plain HTTP and
   private addresses **only** for hosts in the new setting `LOCAL_INFERENCE_HOSTS` (comma list, default empty); every other
   rule unchanged; the default client is unchanged.

## Tests (write first)
`test_non_transactional_job_runs_without_open_transaction_and_records_success`,
`test_non_transactional_job_failure_retries_like_others`, `test_per_business_cron_enqueues_one_job_per_active_business`,
`test_active_business_ids_definer_is_safe` (search_path, grants), `test_assistant_proposal_on_behalf_cannot_be_self_approved`
(and allowed when the business allows self-approval), `test_bind_hold_then_reject_releases_hold`,
`test_bind_hold_rejects_other_business_or_bound_hold`, `test_get_message_context_fields_and_isolation`,
`test_find_open_proposals_filters`, `test_local_client_allows_only_listed_hosts`, `test_default_client_unchanged`.

## Done when
`make check` passes (all existing job, scheduling, approvals and http tests still pass); module READMEs and
`docs/roadmap/07-workflow-engine.md` (jobs section) updated; `PRE_EVENT_WORK.md` entry; second-model review of items 1–3
and 7 done and findings fixed.

## Out of scope
Any AI code.
