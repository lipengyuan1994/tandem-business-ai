# PQ-01 · Procurement groundwork: quote requests, supplier quotes, comparison, late orders (backend)

Kind **PRE** (ordinary ERP features, no AI) · Tier A · Size L · Depends on: PR-02 (per-business cron) · Blocks: PQ-02, AI-12

## Goal
Let a business request quotes from suppliers, receive them (through a signed link or typed in by staff), compare them
with a **deterministic, explained score**, turn the best into a purchase order, and see and chase late orders. All of it
works by hand; later the AI (AI-12) drafts the same proposals. Numbers always come from this code, never from a model.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/integration-map.md` §B rows marked (PQ-01) and §D rows marked (PQ-01): the exact signatures and
   payloads you must provide.
2. `docs/roadmap/modules/M17-purchasing.md` (whole file, including "As built").
3. `backend/app/modules/purchasing/` — `README.md`, `models.py`, `schemas.py`, `public.py`, `executors.py`,
   `registration.py`; skim `service.py`, `service_receipts.py`, `service_suppliers.py` function names only.
4. `backend/app/modules/marketing/service.py` lines 560–600 (`make_token` / token check): copy this signed-token pattern.
5. `backend/app/modules/notifications/public.py` (`enqueue_message`) and `templates.py` (add templates here).
6. `backend/app/modules/dependencies.py`; `docs/roadmap/04-repository-and-conventions.md` "Code patterns".

## You build (module `purchasing`; one migration after the current head)
1. **Tables** (business-scoped, forced RLS via `scoped_table`): `quote_requests` (status `draft|sent|closed|cancelled`,
   `needed_by date`, `reply_by date`, `message`, `created_by`), `quote_request_lines` (item_id, quantity),
   `quote_request_suppliers` (supplier_id, `token_hash`, status `invited|responded|declined|expired`, `sent_at`,
   `responded_at`), `supplier_quotes` (request_id, supplier_id, currency, lead_time_days, min_quantity, valid_until,
   notes ≤ 500, `source form|staff|assistant_extracted`, status `received|accepted|rejected`, version),
   `supplier_quote_lines` (item_id, unit_price_minor, quantity_available). On `purchase_orders`: add `sent_at` (set by the
   mark-sent transition) and `expected_delivery_date` (set when sent: sent date + the supplier's lead time for the
   slowest line; editable while not received).
2. **Executors** (approve permission in parentheses): `purchasing.send_quote_requests` (purchasing.po.approve) — creates the
   request, one signed token per supplier, sends one e-mail per supplier through the outbox (template `quote_request`,
   link `https://<host>/q/<token>`; suppliers without an e-mail are rejected at validation);
   `purchasing.record_supplier_quote` (purchasing.po.write); `purchasing.create_po_from_quote` (purchasing.po.approve) —
   creates a **draft** PO from the quote lines (the normal PO approval still applies) and marks the quote `accepted`;
   `purchasing.send_supplier_followup` (purchasing.po.write) — outbox e-mail, template `supplier_followup`.
   Payloads exactly as integration-map §D.
3. **Public supplier form API** (no login): `GET /api/v1/public/quotes/{token}` (business name, lines, needed_by,
   reply_by) and `POST /api/v1/public/quotes/{token}` (unit prices per line, lead time, valid until, notes; or
   `declined: true`). Token: HMAC like marketing, purpose `quote1`, bound to request+supplier, expires at `reply_by`
   + 7 days; store only its hash. Rate limit like the public chat. Each POST creates or replaces that supplier's quote
   (source `form`) and sets the invite to `responded`.
4. **Reads** in `purchasing/public.py` (exact names and fields from integration-map §B):
   `list_suppliers_for_item`, `get_quote_request`, `compare_quotes`, `list_late_purchase_orders`.
5. **Comparison score (deterministic, frozen formula, explained per component):** per quote that covers all lines:
   price score = cheapest total / this total (0–1); lead score = 1 if delivery (today + lead time) ≤ needed_by, else
   max(0, 1 − days late / 14); reliability = supplier on-time rate over its last 10 received POs (received on or before
   `expected_delivery_date`), 0.8 when it has fewer than 3. **Total = 0.6 × price + 0.25 × lead + 0.15 × reliability**,
   rounded to 3 decimals; ties → lower total price, then earlier lead time. **Eligibility before ranking** (each
   ineligible quote is listed with its reason, not ranked): covers every line (`incomplete`); same currency as the
   business default (`other_currency`); `valid_until` absent or ≥ today (`expired`); `min_quantity` absent or ≤ the
   requested quantity and `quantity_available` absent or ≥ requested (`quantity`); total > 0 (`invalid_total`). Return
   every component so the UI and the AI can explain it.
6. **Staff API** (authenticated) for the manual flow: create/list/get quote requests, enter a quote by hand, compare,
   create PO from quote, send follow-up, list late POs. Staff writes go through staff proposals for the executors above,
   like the existing purchasing actions.
7. Per-business cron `purchasing-expire-quote-invites` (daily; uses PR-02 `per_business=True`): invites past
   `reply_by` + 7 days → `expired`.
8. **Event and scan reads** (integration-map §E and §B "Inputs and guards"): publish `purchasing.quote_received`
   (quote_request_id, supplier_id, declined, all_answered — declined and expired count as answered) whenever a quote is
   recorded or declined; `list_quote_requests_due` and `list_unanswered_invites` in `public.py`; `last_followup_at` on
   invites and purchase orders, set by `send_supplier_followup`.
9. Dependencies: purchasing may now import `notifications` (outbox); update `dependencies.py`,
   `docs/roadmap/02-architecture.md` and `docs/roadmap/modules/README.md` rows together.

## Tests (write first; names are the contract)
`test_send_quote_requests_emails_each_supplier_once_with_signed_link`, `test_quote_token_is_single_supplier_and_expires`,
`test_public_quote_post_creates_quote_and_marks_responded`, `test_public_quote_rate_limited`,
`test_compare_quotes_formula_components_and_tie_break`, `test_compare_eligibility_reasons_currency_validity_quantity_total`, `test_quote_received_event_all_answered`,
`test_list_quote_requests_due_and_unanswered_invites`,
`test_reliability_uses_expected_delivery_and_defaults_for_new_suppliers`,
`test_create_po_from_quote_creates_draft_po_needing_approval`, `test_expected_delivery_date_set_on_send`,
`test_list_late_purchase_orders_business_timezone`, `test_followup_email_through_outbox`,
`test_cross_business_token_or_ids_rejected`, `test_erasure_and_export_cover_new_tables`.

## Done when
`make check` passes; `make gen-api`; M17 "As built" and the module README updated; `PRE_EVENT_WORK.md` entry.
A second-model review (Tier A) of the token, scoring and executors is done and its findings fixed.

## Out of scope
Any AI code (extraction of free-text replies is AI-12), inbound e-mail parsing, supplier portals with login.
