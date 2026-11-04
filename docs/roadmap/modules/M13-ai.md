# M13 · ai

| Field | Value |
|---|---|
| Release / phase | MVP · P5 |
| Tier / kind | **A** · **EVENT (build only after 2026-11-03 15:00 UTC)** |
| Depends on | platform, org, identity, crm, catalog, scheduling, inventory, conversations, marketing, approvals |
| Owns tables | `ai_runs`, `ai_run_steps`, `assistant_threads`, `assistant_messages`, `ai_usage_daily`; stretch: `knowledge_documents`, `knowledge_chunks` |

> Build from the [AI build kit](../ai/README.md); its [contracts](../ai/contracts.md) supersede the tables, API and
> tests listed here where they differ.

## Purpose
Everything AI: the inference gateway and routing, prompts, the agent runner and tools, the use cases (intake, copilot, summaries, drafts, briefing, reorder note), usage metering and budgets, and the evaluation runner. Full design: [08-ai-layer.md](../08-ai-layer.md). This file lists the contract.

## Tables
`ai_runs` — `use_case` (`intake` \| `copilot` \| `summary` \| `draft_reply` \| `campaign_draft` \| `briefing` \| `reorder_note`), `trigger_type`, `trigger_id`, `on_behalf_of_user_id`, `prompt_name`, `prompt_version`, `model_class`, `provider`, `model`, `status` (`running` \| `succeeded` \| `failed` \| `step_limit` \| `budget_exceeded` \| `policy_blocked`), `steps`, `input_tokens`, `output_tokens`, `cost_micros` (USD × 10⁶), `latency_ms`, `error_code`, `started_at`, `finished_at`.

`ai_run_steps` — `run_id`, `step_no`, `kind` (`tool_call` \| `final` \| `repair`), `tool`, `arguments_redacted jsonb`, `result_summary` (≤ 500 chars), `input_tokens`, `output_tokens`, `latency_ms`.

`assistant_threads` — `user_id`, `title`. `assistant_messages` — `thread_id`, `role` (`user` \| `assistant`), `content`, `run_id`, `proposal_ids uuid[]`. Retention 90 days.

`ai_usage_daily` — `(business_id, day)` primary key, `input_tokens`, `output_tokens`, `cost_micros`, `runs`. Updated in the same transaction as run completion.

## Business rules
1. All model calls go through `gateway.router` honouring the business `ai_policy`. A blocked policy → run status `policy_blocked`, and the UI explains why.
2. Before each step, check the business daily budget. Over budget → stop with `budget_exceeded`.
3. Tools enforce `ctx.require(tool.permission)`. The intake context is a system assistant context ([08](../08-ai-layer.md#tools)).
4. The agent can create proposals and holds only. Any other mutation path is a defect (covered by a test that runs every tool against a read-only database role, except propose/hold tools).
5. Intake is idempotent per message (`dedupe_key = "intake:{message_id}"`).
6. If customer matching is ambiguous (top two candidates within 0.1 score, or the best below 0.8), intake must not pick one. It posts an internal note asking staff to choose and stops with `needs_human`.
7. Intake runs only for inbound messages from visitors or customers, never for staff or assistant messages (prevents loops).
8. Prompt files are versioned. Changing a prompt means a new version file plus an eval run.

## Public interface (`public.py`)
```python
def run_intake(db, ctx, message_id: UUID) -> RunResult            # also the job handler
def is_enabled(db, ctx) -> AiStatus                                # policy + budget state for UI banners
```

## API
| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/ai/copilot/threads` | `ai.assistant.use` | Create thread |
| POST | `/ai/copilot/threads/{id}/messages` | `ai.assistant.use` | **SSE stream** of events: `step` (tool name + short status), `proposal` (id + title), `final` (text), `error` |
| GET | `/ai/copilot/threads/{id}` | `ai.assistant.use` (own) | |
| POST | `/ai/conversations/{id}/summary` | `conversations.thread.read` + `ai.assistant.use` | Summary + suggested reply proposal |
| POST | `/ai/campaigns/{id}/draft` | `marketing.campaign.write` + `ai.assistant.use` | Adds a campaign revision |
| GET | `/ai/briefing` | `ai.assistant.use` | Cached hourly per business |
| GET | `/ai/runs` | `ai.settings.read` | Run history with cost and latency |
| GET | `/ai/usage` | `ai.settings.read` | Daily usage vs budget |
| GET / PATCH | `/ai/settings` | `ai.settings.read` / `.write` | Policy, budget, debug logging toggle (24 h) |

## Permissions
`ai.assistant.use`, `ai.settings.read`, `ai.settings.write`.

## Events
Subscribed: `conversations.message_received` (→ job `ai.intake`), `inventory.stock_below_reorder_level` (→ job `ai.reorder_note`), `approvals.proposal_closed` (statistics: approval rate per use case).

## Jobs
`ai.intake`, `ai.reorder_note`, `ai.briefing_refresh` (hourly cron, only for businesses active in the last 24 h).

## Error codes
`ai.disabled_by_policy`, `ai.budget_exceeded`, `ai.provider_unavailable`, `ai.invalid_model_output`, `ai.step_limit`, `ai.tool_not_allowed`.

## UI
Assistant slide-over (Ctrl/Cmd + J) with streaming steps ("Checking availability…"), proposal chips linking to Approvals, and a clear "AI can make mistakes; you approve every change" line. Inbox "Summarise" and "Suggest reply". AI settings page with policy explanation in plain words, budget, usage chart and run history.

## Tests (must exist)
- Gateway: `test_openai_compat_adapter_parses_usage_and_json` (recorded fixtures, no live calls in `make check`), `test_router_respects_strict_private_policy`, `test_fallback_on_timeout_to_next_route`
- Runner: `test_invalid_json_gets_one_repair_then_fails`, `test_step_limit`, `test_budget_exceeded_stops_run`, `test_tool_permission_denied_returns_observation_not_crash`
- Safety: `test_tools_have_no_business_id_argument`, `test_read_tools_cannot_mutate` (read-only role), `test_untrusted_content_is_fenced_and_escaped`
- Intake: `test_intake_idempotent_per_message`, `test_ambiguous_customer_requires_human`, `test_intake_ignores_staff_and_assistant_messages`
- Evals (`make eval`, live, not in `make check`): thresholds in [08](../08-ai-layer.md#evaluation)
