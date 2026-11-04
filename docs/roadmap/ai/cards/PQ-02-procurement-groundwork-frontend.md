# PQ-02 · Procurement groundwork screens

Kind **PRE** (no AI) · Tier B · Size M · Depends on: PQ-01 · Blocks: AI-13 (procurement parts)

## Goal
Make the PQ-01 flow usable by a shop owner: request quotes, see who answered, compare with the explained score, turn the
best quote into a purchase order, chase late orders; and give suppliers a simple public page to answer.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/09-frontend.md` (rules) and `frontend/src/features/purchasing/` (all files: your template and home).
2. The PQ-01 section of `backend/openapi.json` (paths `/api/v1/purchasing/quote-requests*`, `/api/v1/public/quotes/*`).
3. `frontend/src/widget/` (the separate public bundle; the supplier page joins it) and `deploy/compose/Caddyfile`
   (how `/p/*` reaches `widget.html`).
4. `frontend/src/components/common/` file names (reuse: DataTable, StatusBadge, QueryError, EmptyState, dialogs).

## You build
- Purchasing gets a **Quote requests** tab: list (status, item, suppliers answered x of y, reply-by); "Request quotes"
  dialog (items with quantity, needed-by, reply-by, choose 1–5 suppliers that sell the item, optional message);
  request page with invited suppliers and their status, received quotes, **comparison table** showing total, lead time,
  reliability and the score components with a one-line plain explanation of the formula, actions "Enter quote" (staff
  typing a supplier's answer), "Create purchase order from this quote", "Send follow-up".
- **Late orders**: a filter on the Purchase orders tab and a card on Today ("2 purchase orders are late").
- **Supplier quote page** at `/q/:token` in the public bundle: business name, lines, inputs per line, lead time, valid
  until, notes, "We cannot quote" option; plain, mobile-first, no login, no tracking. Route `/q/*` to `widget.html` in
  `Caddyfile` and `Caddyfile.dev`, and add a smoke check in `deploy/scripts/smoke.sh`.
- Money input in normal currency units (reuse the decimal helper from sales/purchasing). Strings in `en.json`.

## Tests
Component tests (msw): request dialog validation and payload; comparison table renders components and ranks;
"Create purchase order" calls the right endpoint; supplier page submits and shows a thank-you; expired token shows a
clear message. Add `/purchasing` quote tab and `/q/<token>` to the a11y e2e list; one e2e: request quotes → supplier
answers through the public page → compare → create PO.

## Done when
`pnpm exec tsc -b`, `pnpm lint`, `pnpm exec vitest run`, `make e2e`, `make check` pass; widget bundle stays ≤ 80 KB gzip
(report the number); `PRE_EVENT_WORK.md` entry.

## Out of scope
AI buttons (AI-13 adds "Paste supplier reply" later).
