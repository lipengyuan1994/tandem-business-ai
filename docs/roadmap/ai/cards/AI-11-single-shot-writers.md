# AI-11 · U3 thread summary + suggested reply, U4 reorder note, U6 daily briefing

Kind **EVENT** · Tier B (Tier-A review) · Size M · Depends on: AI-05 (structured completion), AI-07 (`propose_reply`,
`propose_reorder`), PR-01 (`dashboard.today_summary`) · Blocks: AI-13 parts, AI-15

## Goal
Three one-call features that save staff time: summarise a conversation and draft a reply; explain a reorder suggestion
in plain words and turn it into a draft proposal; give the owner five bullet points for today.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/architecture.md` §5 (single-shot), §6 (numbers come from code).
2. `docs/roadmap/ai/contracts.md` §9 (`complete`), §11 (`summary`, `reorder_note`, `briefing` and their output models),
   §13 (reorder subscriber and job), §14 (`/conversations/{id}/summary`, `/briefing`).
3. `docs/roadmap/ai/integration-map.md` §B (`get_conversation`, `list_low_stock`, `today_summary`), §D (`propose_reply`,
   `propose_reorder`), §E (`inventory.stock_below_reorder_level`).
4. `docs/roadmap/ai/testing.md` §1, §4.
5. `backend/app/modules/ai/structured.py`, `tools/proposals.py` (signatures only).

## You build
- `ai/usecases/summary.py`: read the thread (last 30 messages, fenced) → `ThreadSummary` (model class `fast_extract`,
  prompt `summary`) → one `conversations.send_reply` proposal with `suggested_reply` (via the same code path as the
  `propose_reply` tool, evidence = the conversation). Endpoint `POST /ai/conversations/{id}/summary`.
- `ai/usecases/reorder_note.py`: subscriber `ai.reorder-trigger` on `inventory.stock_below_reorder_level` → job
  `ai.reorder_note` (non-transactional, dedupe per item per UTC day; AI-12 later adds a second enqueue to the same
  subscriber) → read `list_below_reorder` → `ReorderNote` (writer) explaining **the suggestion's numbers as given**
  → one `inventory.create_reorder_draft` proposal with quantity = `suggested` and the note as `reason_text`. If a reorder
  proposal for that item is already pending, do nothing.
- `ai/usecases/briefing.py`: `GET /ai/briefing` — **per user**: run with `staff_ctx_factory(..., "briefing")` so
  `today_summary` only sees what this user may see; `trigger_key = f"briefing:{user_id}:{UTC hour}"`; if a succeeded run
  with that key exists, return its `result`; otherwise `today_summary` → `Briefing` (writer), stored in the run's `result`.
- **Prompts** `summary.v1.md`, `reorder_note.v1.md`, `briefing.v1.md`: fencing sentence; use only the given facts; never
  change or invent numbers; reply text friendly, short, in the business's voice, no promises of price or time not in the
  facts; output = the §11 model.
- **Check after the model (code):** for `reorder_note` and `briefing`, every number in the output must appear in the input
  facts; otherwise reject the output as `ai.invalid_model_output` (one repair).

## Tests (write first)
`test_summary_creates_one_reply_proposal_with_evidence`, `test_summary_requires_thread_read_and_ai_permission`,
`test_reorder_note_quantity_from_inventory_not_model`, `test_reorder_note_rejects_invented_numbers`,
`test_reorder_note_skips_when_pending_proposal_exists`, `test_briefing_cached_one_hour_per_user`, `test_briefing_respects_user_permissions`,
`test_briefing_rejects_invented_numbers`, `test_policy_blocked_returns_409_with_reason`.

## Done when
`make check` passes; `make gen-api`; `HANDOFF.md` rewritten.
