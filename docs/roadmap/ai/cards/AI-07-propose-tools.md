# AI-07 · Propose and hold tools

Kind **EVENT** · Tier A · Size M · Depends on: AI-06, PR-02 (`bind_hold`), PR-01/PQ-01 for their tools · Blocks: AI-08B, AI-10, AI-12

## Goal
The only ways the AI can cause change: create proposals in the approvals engine (a person approves, the existing
executor acts) and place a reversible scheduling hold.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/architecture.md` §1, §6.
2. `docs/roadmap/ai/contracts.md` §8 (observation JSON), §10.
3. `docs/roadmap/ai/integration-map.md` §C, §D (exact payloads), §F (hold).
4. `docs/roadmap/ai/testing.md` §3.
5. `backend/app/modules/ai/tools/` (framework and read tools) and `backend/app/modules/approvals/public.py`.

## You build
`ai/tools/proposals.py` (and the propose part of `purchasing.py`/`sales.py` if PQ-01/PR-01 landed) with:
`hold_slot`, `propose_booking`, `propose_reply`, `propose_customer`, `propose_reorder`, `propose_payment_reminder`,
`propose_quote_request`, `propose_supplier_quote`, `propose_po_from_quote`, `propose_supplier_followup`.
Rules:
- Args models mirror integration-map §D, but **the tool fills ids the model must not invent**. Booking follows the
  frozen booking rules in contracts §10: existing customer → `hold_id` from `hold_slot` in this run (checked against
  `RunState.holds`), proposal created and hold bound with `scheduling.bind_hold` in the same transaction; new customer →
  no hold, `customer: {"new": …}` built from `RunState.server_facts`, and the proposal evidence notes "slot not held".
  `hold_slot` refuses a customer id that does not exist. `propose_reply` requires a `conversation_id` already in
  `RunState.read_conversation_ids`.
- Each tool builds a short human title (e.g. "Book Brake service for Ada Example, Tue 10 Nov 11:45") from looked-up
  data, not from model text, and attaches evidence: the source message (`kind="message"`) and the tool results used
  (`kind="tool_result"`, excerpt ≤ 500 chars).
- Model-written text (reply body, note, follow-up) is stored as given, length-limited, with no HTML.
- Idempotency across a run **and its retries** comes from the AI-01 framework (`ai_effects`): do not add your own.
- `propose_customer` never sets `consents`; contact points only from `RunState.server_facts` (never from model text).
- `propose_reorder` takes item ids only; **quantities come from `inventory.list_below_reorder`** (`suggested`), not
  from the model.

## Tests (write first)
`test_hold_then_propose_booking_binds_hold_and_rejection_releases_it`, `test_new_customer_booking_without_hold_from_server_facts`, `test_propose_booking_rejects_unknown_or_mismatched_hold`,
`test_propose_reply_requires_read_conversation`, `test_proposal_title_from_data_not_model`,
`test_every_proposal_has_evidence`, `test_propose_customer_never_sets_consent`,
`test_propose_reorder_quantity_from_inventory`, `test_same_call_twice_returns_same_proposal`,
`test_proposal_origin_is_assistant_and_needs_approval`, `test_propose_requires_ai_assistant_use`,
plus one happy path per procurement/sales tool if their functions exist.

## Done when
`make check` passes; `HANDOFF.md` lists the tools and the run-scoped state they rely on (holds, read conversations).
