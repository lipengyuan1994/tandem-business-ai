# ai · M13

Assistant runs, tools and use cases. Interfaces are frozen in `docs/roadmap/ai/contracts.md`; build order and current
state in `docs/roadmap/ai/README.md` and `HANDOFF.md`. Imports other modules only through their `public.py`.
- `ai_runs` — one per run: use case, trigger (`trigger_key`), prompt name/version, model, status, tokens, cost,
  `result` (validated final output only). `ai_run_steps` (append-only) — per step: kind, tool, redacted arguments,
  short summary, tokens. No prompt or completion text is ever stored.
- `ai_effects` (append-only) — holds and proposals a tool created, unique `(business_id, effect_key)`.
- `ai_usage_daily` — tokens, cost, runs per business and UTC day (+ `notified_80_at`). `assistant_*` — copilot; `ai_operations` (migration `20261104_0400_ai_operations`) — scoped operation claims/IDs; `details` (migration `20261104_0500_ai_op_details`) preserves verification metadata.
- `status(db, ctx) -> AiStatus`: reason `ai.not_configured` (no/invalid routes file, or no `agent` route with a usable
  provider key), `ai.disabled_by_policy`, `ai.budget_exceeded`; policy `regulated` when `DATA_PROFILE=regulated`.
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
- `agent/protocol.py`: `ToolCallStep`, `FinalStep`, `AGENT_STEP_ADAPTER`, `ContextBlock`, `RunSpec`, `StepEvent`,
  `RunResult`. `agent/prompts.py`: `load_prompt(name, version=None, *, base_dir=None)` (prompts in `ai/prompts/`).
- `agent/runner.run_agent(ctx_factory, spec, *, router=None, on_event=None) -> RunResult` — never inside a transaction.
- `structured.complete(ctx_factory, *, use_case, prompt, facts, output_model, trigger_type, trigger_key, router=None,
  validate=None) -> (run_id, value)`; raises `AiError`.
- `agent/contexts.py`: `background_ctx_factory(business_id)`, `staff_ctx_factory(business_id, user_id, perms,
  use_case)`. `agent/fencing.fence(block)`; `agent/context.py`: `build_messages`, `append_step`, `trim_to_budget` (schema-aware, enforced by `ModelCaller.call`, including repairs).
- `recording.py`: `RunMeta`, `StepRecord`, `open_run`, `record_step`, `record_usage_now`, `close_run`,
  `close_stale_runs`, `record_early_result` (0-step run for decisions made in code; run id = `ctx.ai_run_id`).
- `usage.py`: `effective_budget`, `check_budget`, `usage_today`, `usage_days`, `maybe_notify_80_percent`.
- `tools/base.py`: `Tool`, `RunState`; `tools/registry.py`: `register_tool`, `get_tool`, `tool_catalog`,
  `execute_tool(ctx, name, raw_args, allowed, state)` (own transaction; errors become observations; hold/propose
  effects recorded in `ai_effects` in the same transaction and replayed by effect key, which includes `trigger_key`).
- 27 tools in `tools/` (registered on import of `tools.registry`): 16 read (`get_conversation`, `find_customers`,
  `get_customer`, `customer_history`, `list_services`, `get_service_parts`, `find_slots`, `list_appointments`,
  `check_stock`, `list_low_stock`, `list_suppliers_for_item`, `get_quote_request`, `compare_quotes`,
  `list_late_purchase_orders`, `list_overdue_invoices`, `today_summary`), `hold_slot` (the only direct write) and 10
  propose tools (`propose_booking`, `_reply`, `_customer`, `_reorder`, `_payment_reminder`, `_quote_request`,
  `_supplier_quote`, `_po_from_quote`, `_supplier_followup`, `_send_po`). Read tools never return contact values (free text is
  redacted) and add evidence to `RunState.evidence`; titles come from looked-up data, never model text.
- Intake (U1): `conversations.message_received` (visitor/customer) → job `ai.intake` (non-transactional) →
  `usecases.intake.run(message_id, business_id)`: open-proposal guard, safety screen (`usecases.safety.screen`,
  prompt `prompts/safety.v1.md`), customer matching, agent step, one outcome note per message.
- `permissions.py` (§3 sets), `errors.py` (`AiError`), `settings.py` (`ai_settings()`, `api_key(env)`),
  `schemas.py` (§11 outputs, run/usage DTOs), `lifecycle.py` (export, erasure).
`GET /status` (`ai.assistant.use`) → `AiStatus`; `GET /runs?cursor&use_case&status&limit` (`ai.settings.read`) →
`Page[RunOut]` (no text); `GET /usage` (`ai.settings.read`) → `UsageOut{today, days (30, oldest first)}`.

Config: `deploy/config/ai-routes.example.yaml` (placeholders), `ai-routes.dev.yaml` + `ai-fake-script.dev.yaml` (dev, no key).
- Intake agent (AI-08B): prompt `intake.v1`, tools to read, hold and propose one booking or one question.
- Copilot (U2, `usecases/copilot.py`): `/copilot/threads*` (own threads, `CopilotThreadOut`), SSE message stream,
  tools limited to the user's permissions, 30 runs/user/hour, cron + job `ai.assistant_retention` (90 days). Earlier
  assistant answers are replayed fenced as untrusted. One active turn/thread, four generations/process; optional operation keys.
- Summary (U3) `POST /conversations/{id}/summary`; reorder note (U4) on `inventory.stock_below_reorder_level`
  (job `ai.reorder_note`); briefing (U6) `GET /briefing` per user per hour; numbers in writer output must appear in the
  facts (code check, one repair); cache identity includes effective permissions. Summary supports operation keys.
- Procurement (U9, `usecases/procurement.py`): request quotes (job + `POST /procurement/items/{id}/request-quotes`),
  extract a pasted supplier reply (`POST /procurement/quote-requests/{id}/extract`, numbers verified against the text; supports operation keys),
  compare and propose a PO (`ai.compare-trigger`), chase late orders/invites (cron `ai-procurement-scan`).
  `procurement_fake.py` only adapts the dev scripted provider (refused in production).
- Evals: `backend/evals/` (136 cases, `AI_LIVE=1 make eval suite=…`); safety review `docs/security/2026-11-03-ai-review.md`.
- Payment follow-up (U10): paged scan → deduplicated per-invoice jobs; proposals lock/recheck version, pending proposals
  and cooldown. Live-provider verification remains owner-run.

P10-A0: `/autonomy/rules` GET/PUT (owner/admin via `org.settings.write`, versioned), `/autonomy/pause|resume`
POST, `/autonomy/activity` GET (`approvals.proposal.read`, cursor pagination). Policy is owned by approvals;
org exposes `ai_autonomy_paused` and atomic pause/resume. Only AI reorder-draft proposals are eligible.
U4 checks open proposals (including executing) and inventory's open-draft intent before generation and proposing;
`off` ends the job without model calls/retries (also handled if changed during generation). Quantity and stock are
re-checked under stock locks at execution. Shadow is the seeded default. See contracts “Autonomy rules (P10-A0)”.

P10-A1: copilot.v3 keeps v1/v2; bounded staff quantities are copied, unchanged suggestions accepted, errors guide retry.
U4 stays computed-only. `copilot_threads.py` owns thread DTOs/reads/retention;
`copilot_cards.py` verifies own thread + linked copilot run/requester and provides explicit Confirm.
Cards carry revision/hash, preview, eligibility/reason, delivery state and whitelisted result UUIDs;
current status/results are fenced context on later turns. Typed yes never approves. Read-only
`/work-queue` and `/exceptions` require proposal read, merge scoped public readers with pagination;
exceptions includes current pause and seven-day fallback history. Proposal tool results supply fresh
confirmation eligibility; v3/FinalStep reserve needs_human for blockers, not advice, stock warnings or ordinary Confirm.
