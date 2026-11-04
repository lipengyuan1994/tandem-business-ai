# AI-06 · Read tools

Kind **EVENT** · Tier A · Size M · Depends on: AI-01 (tool framework) · Blocks: AI-07

## Goal
Typed, permission-checked read tools that let the model look at customers, conversations, services, free slots,
stock, suppliers, quotes and overdue invoices — each a thin wrapper around one existing `public.py` function.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/contracts.md` §3, §8 (observation JSON), §10.
2. `docs/roadmap/ai/integration-map.md` §B (every row: the exact function per tool) and §G (fixtures).
3. `docs/roadmap/ai/testing.md` §3.
4. `backend/app/modules/ai/tools/base.py`, `registry.py` (if they exist).
5. For each wrapped module, its `public.py` only (crm, conversations, catalog, scheduling, inventory, purchasing,
   sales, dashboard). Do not open their services.

## You build
`ai/tools/crm.py`, `conversations.py`, `catalog.py`, `scheduling.py`, `inventory.py`, `purchasing.py` (read part),
`sales.py`, `dashboard.py`, registered in `registry.py`. One `Tool` per read row of contracts §10 whose wrapped
function exists. Rules:
- `args_model` with `extra="forbid"`, ISO datetimes with time zone, UUIDs as strings in JSON; `find_slots` caps the
  window at 14 days and `limit` at 10.
- Returned data is **small**: only the fields listed in integration-map §B, money as `{"minor": int, "currency": str}`,
  quantities as strings, times in UTC plus the business-local label where the DTO has one. Truncate lists to 20 items.
- Customer contact details are never returned by read tools (only `contact_types`); `list_overdue_invoices` drops
  `bill_to_email`. `get_service_parts` returns the service's parts (item ids, names, quantities).
- `get_conversation` adds the conversation id to `RunState.read_conversation_ids`.
- If a wrapped function is marked (PR-01)/(PQ-01) and does not exist yet, skip that tool and list it in HANDOFF.

## Tests (write first)
For each tool: a happy-path test on a seeded business and a permission-denied test.
Plus `test_tools_have_no_business_id_argument`, `test_read_tools_cannot_mutate` (row counts unchanged),
`test_read_tools_return_no_contact_values`, `test_find_slots_window_capped`, `test_tool_catalog_schema_is_json`,
`test_other_business_ids_not_found` (ids of another business give a not-found observation).

## Done when
`make check` passes; `HANDOFF.md` lists the tools that exist and any skipped.

## Out of scope
Propose and hold tools (AI-07).
