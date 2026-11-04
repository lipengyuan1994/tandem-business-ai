# AI-10 · U2 Copilot chat over Server-Sent Events

Kind **EVENT** · Tier A · Size M · Depends on: AI-05, AI-07 · Blocks: AI-13 (assistant panel integration)

## Goal
Staff ask questions in plain words ("Who is booked tomorrow?", "What runs out this week?", "Draft a reorder for low
stock") and see the assistant's steps stream in, with answers grounded in tool results and any proposals linked.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/architecture.md` §4 (staff context), §5 (U2).
2. `docs/roadmap/ai/contracts.md` §4 (`assistant_threads`, `assistant_messages`), §8 (`StepEvent`, `run_agent`
   `on_event`), §10 (tools used by U2), §13 (retention cron), §14 (copilot rows, SSE format, rate limit).
3. `docs/roadmap/ai/testing.md` §4.
4. `backend/app/modules/ai/` — `agent/runner.py`, `tools/registry.py`, `api.py`.
5. `deploy/compose/Caddyfile` and `Caddyfile.dev` (the `/api/*` reverse proxy blocks).

## You build
- API endpoints of §14 for threads (`GET /copilot/threads`, `POST /copilot/threads`, `GET /copilot/threads/{id}`
  with `ThreadOut` / `ThreadDetailOut`) and messages; the message endpoint returns a `StreamingResponse` that runs the agent in
  a worker thread and yields `StepEvent`s as SSE; client disconnect stops after the current step.
- Context: `staff_ctx_factory(business_id, user_id, user_permissions, "copilot")` (contracts §3, §8); tools =
  every read tool plus `propose_reply`, `propose_reorder`, `propose_booking`, `hold_slot`, `propose_customer`; the last 10
  thread messages are included as context blocks with `untrusted=False` (staff are authenticated), while tool results
  and any customer or supplier text reached through tools stay fenced as untrusted.
- `status_text` per tool (plain words, e.g. `find_slots` → "Checking free times…") in one dictionary.
- Persist the user message and the final assistant message (with `run_id`, `proposal_ids`).
- Rate limit 30 runs per user per hour (`app.platform.ratelimit.hit`).
- Caddy: `flush_interval -1` for `/api/v1/ai/copilot/*` in both Caddyfiles; validate with `caddy validate`.
- Per-business cron + non-transactional job `ai.assistant_retention` (delete messages older than 90 days; threads without
  messages).
- **Prompt `ai/prompts/copilot.v1.md`**: role (helpful assistant for this business's staff), answer only from tool results,
  say "I don't know" otherwise, keep answers short, create proposals only when asked to act, never claim an action is done
  (it is proposed), fencing sentence, output = `AgentStep`.

## Tests (write first)
`test_sse_stream_emits_step_proposal_final_in_order`, `test_sse_error_event_on_budget_exceeded`,
`test_copilot_tools_limited_by_user_permissions`, `test_thread_owner_only`, `test_rate_limit_30_per_hour`,
`test_messages_persisted_with_run_and_proposals`, `test_list_threads_own_only_newest_first`, `test_retention_job_deletes_old_messages`,
`test_no_db_transaction_held_while_streaming`.

## Done when
`make check` passes; `caddy validate` passes for both Caddyfiles; manual: with the dev `fake` provider the stream is
visible with `curl -N`. `HANDOFF.md` rewritten.
