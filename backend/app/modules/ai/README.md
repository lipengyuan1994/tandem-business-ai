# ai · M13

Assistant runs, tools and use cases. Interfaces are frozen in `docs/roadmap/ai/contracts.md`; build order and current
state in `docs/roadmap/ai/README.md` and `HANDOFF.md`. Imports other modules only through their `public.py`.

## Tables (migration `20261103_1600_ai_core`; all business-scoped, forced RLS)
- `ai_runs` — one per run: use case, trigger (`trigger_key`), prompt name/version, model, status, tokens, cost,
  `result` (validated final output only). `ai_run_steps` (append-only) — per step: kind, tool, redacted arguments,
  short summary, tokens. No prompt or completion text is ever stored.
- `ai_effects` (append-only) — holds and proposals a tool created, unique `(business_id, effect_key)`.
- `ai_usage_daily` — tokens, cost, runs per business and UTC day (+ `notified_80_at`). `assistant_*` — copilot.

## Public (`public.py`)
- `status(db, ctx) -> AiStatus`: reason `ai.not_configured` (no/invalid routes file, or no `agent` route with a usable
  provider key), `ai.disabled_by_policy`, `ai.budget_exceeded`; policy `regulated` when `DATA_PROFILE=regulated`.

## Gateway (`gateway/`)
- `types.py`: `ChatMessage`, `ChatRequest`, `ChatResult`, `Usage`, `ProviderError(kind, detail, retry_after_s)`,
  `TRANSIENT_KINDS`, `Adapter`. Adapters `openai_compat.OpenAICompatAdapter(name, base_url, api_key, *, local)` and
  `anthropic.AnthropicAdapter(name, base_url, api_key)` use `app.platform.http.client()`; content is never logged.
- `routes.py`: `RoutesFile`/`ProviderCfg`/`RouteEntry`/`Limits`, `load_routes_file(path)` (`RoutesFileError` names
  the bad key). `pricing.cost_micros(entry, usage)`.
- `router.py`: `Router(routes, adapters)` with `.limits`, `.allowed(class, policy)`, `.complete(class, policy, req)`
  (fallback on transient errors; `PolicyBlocked`); `load_router()` (cached, None without a valid file),
  `reset_router_cache()`, `set_router_for_tests(r)`, `effective_policy(db, ctx)`, `check_routes_at_startup()` (prod
  refuses the `fake` provider).
- `fake.py`: `FakeGateway(script | fn)` with `.calls`, `fake_router(gw)`, `ScriptFileAdapter` (`AI_FAKE_SCRIPT_FILE`).

## Runner (`agent/`, `structured.py`, `recording.py`, `usage.py`)
- `agent/protocol.py`: `ToolCallStep`, `FinalStep`, `AGENT_STEP_ADAPTER`, `ContextBlock`, `RunSpec`, `StepEvent`,
  `RunResult`. `agent/prompts.py`: `load_prompt(name, version=None, *, base_dir=None)` (prompts in `ai/prompts/`).
- `agent/runner.run_agent(ctx_factory, spec, *, router=None, on_event=None) -> RunResult` — never inside a transaction.
- `structured.complete(ctx_factory, *, use_case, prompt, facts, output_model, trigger_type, trigger_key, router=None,
  validate=None) -> (run_id, value)`; raises `AiError`.
- `agent/contexts.py`: `background_ctx_factory(business_id)`, `staff_ctx_factory(business_id, user_id, perms,
  use_case)`. `agent/fencing.fence(block)`; `agent/context.py`: `build_messages`, `append_step`, `trim_to_budget`.
- `recording.py`: `RunMeta`, `StepRecord`, `open_run`, `record_step`, `record_usage_now`, `close_run`,
  `close_stale_runs`, `record_early_result` (0-step run for decisions made in code; run id = `ctx.ai_run_id`).
- `usage.py`: `effective_budget`, `check_budget`, `usage_today`, `usage_days`, `maybe_notify_80_percent`.

## Tool framework (`tools/`, `effects.py`)
- `tools/base.py`: `Tool`, `RunState`; `tools/registry.py`: `register_tool`, `get_tool`, `tool_catalog`,
  `execute_tool(ctx, name, raw_args, allowed, state)` (own transaction; errors become observations; hold/propose
  effects recorded in `ai_effects` in the same transaction and replayed by effect key, which includes `trigger_key`).
- 26 tools in `tools/` (registered on import of `tools.registry`): 16 read (`get_conversation`, `find_customers`,
  `get_customer`, `customer_history`, `list_services`, `get_service_parts`, `find_slots`, `list_appointments`,
  `check_stock`, `list_low_stock`, `list_suppliers_for_item`, `get_quote_request`, `compare_quotes`,
  `list_late_purchase_orders`, `list_overdue_invoices`, `today_summary`), `hold_slot` (the only direct write) and 9
  propose tools (`propose_booking`, `_reply`, `_customer`, `_reorder`, `_payment_reminder`, `_quote_request`,
  `_supplier_quote`, `_po_from_quote`, `_supplier_followup`). Read tools never return contact values (free text is
  redacted) and add evidence to `RunState.evidence`; titles come from looked-up data, never model text.

## Use cases (`usecases/`, `subscribers.py`, `jobs.py`)
- Intake (U1): `conversations.message_received` (visitor/customer) → job `ai.intake` (non-transactional) →
  `usecases.intake.run(message_id, business_id)`: open-proposal guard, safety screen (`usecases.safety.screen`,
  prompt `prompts/safety.v1.md`), customer matching, agent step, one outcome note per message.

## Other files
- `permissions.py` (§3 sets), `errors.py` (`AiError`), `settings.py` (`ai_settings()`, `api_key(env)`),
  `schemas.py` (`AiStatus`, `Policy`, `UseCaseId`, §11 outputs, `RunStatus`, `RunOut`, `UsageToday`, `UsageOut`),
  `lifecycle.py` (export, erasure).

## API (`/api/v1/ai`)
`GET /status` (`ai.assistant.use`) → `AiStatus`; `GET /runs?cursor&use_case&status&limit` (`ai.settings.read`) →
`Page[RunOut]` (no text); `GET /usage` (`ai.settings.read`) → `UsageOut{today, days (30, oldest first)}`.

## Config
`deploy/config/ai-routes.example.yaml` (placeholders), `ai-routes.dev.yaml` + `ai-fake-script.dev.yaml` (dev, no key).

- Intake agent (AI-08B): prompt `intake.v1`, tools to read, hold and propose one booking or one question.
- Copilot (U2, `usecases/copilot.py`): `/copilot/threads*` (own threads, `CopilotThreadOut`), SSE message stream,
  tools limited to the user's permissions, 30 runs/user/hour, cron + job `ai.assistant_retention` (90 days). Earlier
  assistant answers are replayed fenced as untrusted.
- Summary (U3) `POST /conversations/{id}/summary`; reorder note (U4) on `inventory.stock_below_reorder_level`
  (job `ai.reorder_note`); briefing (U6) `GET /briefing` per user per hour; numbers in writer output must appear in the
  facts (code check, one repair).
- Procurement (U9, `usecases/procurement.py`): request quotes (job + `POST /procurement/items/{id}/request-quotes`),
  extract a pasted supplier reply (`POST /procurement/quote-requests/{id}/extract`, numbers verified against the text),
  compare and propose a PO (`ai.compare-trigger`), chase late orders/invites (cron `ai-procurement-scan`).
  `procurement_fake.py` only adapts the dev scripted provider (refused in production).
- Evals: `backend/evals/` (130 cases, `AI_LIVE=1 make eval suite=…`); safety review `docs/security/2026-11-03-ai-review.md`.

## Not yet
Live runs and model selection (needs a Vultr Serverless Inference key: AI-04, AI-09 selection), U5 campaign draft and
U10 payment follow-up (AI-15, optional).
