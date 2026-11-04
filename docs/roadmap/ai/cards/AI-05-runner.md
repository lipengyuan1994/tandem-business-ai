# AI-05 · Agent protocol, runner, contexts, structured completion, budgets, recording, run/usage API

Kind **EVENT** · Tier A · Size L · Depends on: AI-03 (router), AI-01 (tables, tool framework) · Blocks: AI-08, AI-08B, AI-10, AI-11, AI-12

## Goal
The engine every AI feature runs on: a bounded, recorded loop in which the model either calls an allowed tool or
finishes, with strict JSON validation, fencing of untrusted text, budgets checked against live usage, crash-safe retries,
and a single-shot variant for summaries and drafts. No real use case or real tool here; tests use a dummy tool.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/architecture.md` §1, §3, §4, §7.
2. `docs/roadmap/ai/contracts.md` §2 (storage rules), §3 (staff context formula), §4, §6 (`Limits`, `Router.complete`),
   §7, §8 (all), §9, §10 (`Tool`, `RunState`, `execute_tool` — already built in AI-01), §12, §14 (`/runs`, `/usage` rows,
   `RunOut`), §15.
3. `docs/roadmap/ai/testing.md` §1.
4. `docs/roadmap/ai/integration-map.md` §A (contexts, transactions).
5. Your module so far (signatures only): `ai/models.py`, `ai/errors.py`, `ai/tools/base.py`, `ai/tools/registry.py`,
   `ai/effects.py`, `ai/gateway/router.py`, `ai/gateway/fake.py`; and `backend/app/modules/identity/public.py`
   (`list_users_with_permission`), `backend/app/modules/notifications/public.py` (`notify_users`).

## You build (exact names from the contracts)
- `ai/agent/protocol.py`: `ToolCallStep`, `FinalStep`, `AgentStep`, `AGENT_STEP_ADAPTER`, `agent_step_json_schema()`,
  `ContextBlock`, `RunSpec`, `RunStatus`, `StepEvent`, `RunResult`.
- `ai/agent/prompts.py`: `Prompt`, `PROMPTS_DIR`, `load_prompt(name, version=None, *, base_dir=None)`; a test prompt in
  `ai/tests/prompts/test_agent.v1.md`.
- `ai/agent/fencing.py`: `fence(block)`; `ai/agent/context.py`: `build_messages`, `append_step`, `trim_to_budget`.
- `ai/agent/contexts.py`: `background_ctx_factory`, `staff_ctx_factory` (contracts §3 formula).
- `ai/recording.py`: `RunMeta`, `StepRecord`, `open_run`, `record_step`, `record_usage_now`, `close_run`,
  `record_early_result` — each in its own short `transaction(ctx)`.
- `ai/usage.py`: contracts §12; `maybe_notify_80_percent` sends with `notifications.public.notify_users` to
  `identity.public.list_users_with_permission(db, ctx, "ai.settings.read")` and sets `ai_usage_daily.notified_80_at`.
- `ai/agent/runner.py`: `run_agent` exactly per contracts §8 "Runner rules" and the status table; copy
  `spec.server_facts` into `RunState.server_facts` and never render it into a message (test it). `on_event` receives
  `step` (with `status_text` = the tool's `description` up to the first period), `proposal` (when a propose tool's
  observation contains `proposal_id`), then `final` or `error`. The runner also collects each proposal's title into
  `RunResult.extra["proposal_titles"]` (same order as `proposal_ids`).
- `ai/structured.py`: `complete` per contracts §9 (a `validate` `ValueError` triggers the single repair).
- `ai/api.py`: `GET /runs` (cursor pagination like other modules) and `GET /usage` (contracts §14).
- `ai/tests/helpers.py`: `step_tool`, `step_final`, `assistant_ctx`, `fake_router_by_class` (testing.md §1).

## Tests (write first)
`test_final_on_first_step_records_succeeded_run_and_usage`, `test_tool_call_then_final_observation_fenced`,
`test_invalid_json_gets_one_repair_then_fails`, `test_unknown_or_disallowed_tool_is_observation`,
`test_tool_permission_denied_is_observation_not_crash`, `test_tool_args_validation_error_is_observation`,
`test_step_limit`, `test_run_timeout_status_and_per_call_timeout`, `test_budget_checked_against_usage_of_running_runs`,
`test_budget_exceeded_before_call_stops_run`, `test_budget_80_percent_notifies_once_per_day`,
`test_policy_blocked_and_not_configured_statuses`, `test_provider_unavailable_status`,
`test_context_trims_oldest_tool_results_first`, `test_untrusted_fence_escaping`, `test_trusted_block_rendering`,
`test_no_prompt_or_completion_text_stored`, `test_structured_complete_validates_and_repairs_on_validate_error`,
`test_interrupted_run_closed_on_retry`, `test_runstate_rebuilt_from_effects_on_retry` (a propose effect from a crashed
attempt is returned, not duplicated), `test_no_db_transaction_open_during_model_call`,
`test_staff_context_permissions_formula`, `test_server_facts_never_in_messages`, `test_runs_and_usage_endpoints_permissions_and_no_text`.

## Done when
`make check` passes; `make gen-api`; `HANDOFF.md` rewritten with the runner's entry points and what use-case authors
must know (building a `RunSpec`, recording early results, choosing a context factory).

## Out of scope
Real tools (AI-06/07), real prompts, use cases, copilot endpoints.
