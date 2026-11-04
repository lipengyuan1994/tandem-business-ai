# AI-01 · `ai` module skeleton: tables, settings, permissions, registration, status

Kind **EVENT** (start ≥ 2026-11-03 15:00 UTC) · Tier A · Size M · Depends on: kickoff, PR-02 · Blocks: everything else

## Goal
Create the empty but real `ai` module: package layout, its tables with row-level security, permissions, registration,
module dependencies, data export/erasure, and a `GET /api/v1/ai/status` that says "AI not configured" until routes exist.
After this card every other card has a place to put code and a database to record runs.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/contracts.md` §1, §2, §3, §4, §10 (tool framework: `Tool`, `RunState`, registry functions,
   `execute_tool` incl. effects), §13 (`AiStatus` only), §14 (`/status` row), §15.
2. `docs/roadmap/ai/integration-map.md` §A (registration API).
3. `docs/roadmap/04-repository-and-conventions.md` "Code patterns".
4. Template module to copy the shape from: `backend/app/modules/marketing/` (`permissions.py`, `models.py`,
   `registration.py`, `lifecycle.py`, `api.py` header) and its migration
   `backend/migrations/versions/20261026_1000_marketing_core.py` (helpers `scoped_columns`, `scoped_table`, append-only).
5. `backend/app/modules/dependencies.py`.

## You build
- `backend/app/modules/ai/` with the files of contracts §1 that this card needs: `__init__.py`, `README.md` (module card),
  `permissions.py` (§3), `models.py` (§4 tables), `schemas.py` (`AiStatus` and the §11 output models as plain Pydantic
  classes), `settings.py` (reads §2 env through `app.platform.config` — add the new settings there), `public.py`
  (`status`), `api.py` (`GET /status`), `registration.py`, `lifecycle.py` (export all AI tables; erasure scrubs
  `ai_runs.result` and `assistant_messages.content` linked to the customer ids/needles using the existing redaction
  helpers), `errors.py` with `AiError` (§15).
- **Tool framework** `ai/tools/base.py` (`Tool`, `ToolKind`, `RunState`), `ai/tools/registry.py` (`register_tool`,
  `get_tool`, `tool_catalog`, `execute_tool`) and `ai/effects.py` (`effect_key(tool, args) -> str`,
  `find_effect(db, ctx, effect_key)`, `record_effect(db, ctx, …)`, `effects_for_trigger(db, ctx, trigger_key)`), exactly
  per contracts §10; tested with a dummy read tool and a dummy propose tool (no real tools yet). Built here so AI-05 and
  AI-06 can run in parallel.
- Migration `2026110x_xxxx_ai_core.py` (head after the current one): tables of §4; `ai_run_steps` append-only;
  RLS forced on all.
- `dependencies.py` row for `ai` per contracts §1, plus the same row in `docs/roadmap/02-architecture.md` and
  `docs/roadmap/modules/README.md` (architecture tests compare them).
- `status()` logic: `enabled=False, reason="ai.not_configured"` when `AI_ROUTES_FILE` is missing or unreadable; otherwise
  `enabled=True` (the router does not exist yet; AI-03 refines this). `policy` = `regulated` when `DATA_PROFILE=regulated`,
  else the business `ai_policy`. `budget_percent` = 0 until AI-05.

## Tests (write first)
`test_ai_tables_have_forced_rls_and_business_scope` (another business sees nothing), `test_ai_run_steps_append_only`,
`test_status_not_configured_without_routes_file`, `test_status_policy_regulated_when_data_profile_regulated`,
`test_status_requires_assistant_permission`, `test_export_includes_ai_tables`, `test_erasure_scrubs_ai_texts`,
`test_register_tool_rejects_duplicates`, `test_execute_tool_observation_shapes`, `test_execute_tool_permission_denied`,
`test_propose_effect_recorded_in_same_transaction_and_replayed`, architecture tests pass.

## Done when
`make check` passes; `make gen-api`; STATUS row DONE; `HANDOFF.md` rewritten (list the files that now exist).

## Out of scope
Gateway, router, runner, real tools (only the framework and dummy test tools), prompts, any model call.
