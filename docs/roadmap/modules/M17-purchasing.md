# M17 · purchasing (v1)

| Field | Value |
|---|---|
| Release / phase | v1 · P8 |
| Tier / kind | B |
| Depends on | platform, org, catalog, inventory, approvals, notifications |
| Owns tables | `suppliers`, `supplier_items`, `purchase_orders`, `purchase_order_lines`, `goods_receipts`, `goods_receipt_lines`, `purchase_order_sequences`, `quote_requests`, `quote_request_lines`, `quote_request_suppliers`, `supplier_quotes`, `supplier_quote_lines` |

## Purpose
Buy stock: suppliers, purchase orders and goods receipts that create `receipt` stock movements. It replaces MVP reorder drafts (a draft can be converted into a PO). **No purchase is sent to a supplier without approval, and there is no automatic purchasing.**

## Key rules
1. PO status: `draft → approved (via proposal or permission) → sent (manual or email v1+) → partially_received → received | cancelled`.
2. A goods receipt records received quantities per line and calls `inventory.record_movement(reason='receipt', ref_type='goods_receipt')`. Over-receipt above 110 % is rejected unless overridden.
3. Purchasing offers `GET /api/v1/purchasing/reorder-suggestions`: it reads `inventory.list_below_reorder` and subtracts outstanding approved, sent and partially received PO quantities. Inventory does not depend on purchasing (owner decision, P8-05).
4. `supplier_items` stores supplier SKU, last price and lead time per item.

## Executors
`purchasing.create_po_from_draft` (AI-proposable from the reorder note).

## Tests
- Receipt creates the right movements; partial receipts; over-receipt guard; PO approval required before `sent`.


## As built (2026-11-03)

- P8-05 is PRE work by owner instruction: backend, frontend and non-AI approval executors only.
- Supplier list/create/edit includes contact details and item SKU, last unit cost and lead time.
  Supplier contact data is exported, excluded from audit changes, and has no customer erasure handler.
- Seven tables: the five original tables plus `goods_receipt_lines` for delivery history and
  `purchase_order_sequences` for independent yearly numbering. All use tenant RLS; receipt tables
  are append-only. The Alembic migration has a downgrade.
- Drafts have no number. Approval locks their lines and assigns `PO-YYYY-NNNN` in the same transaction.
  The sequence rolls back with a failed approval. Draft, approved, sent and partially received orders
  may be cancelled; received orders are final. Cancellation preserves deliveries and assigned numbers.
- Only active stock products are purchasable. Decimal quantities and integer minor-unit costs produce
  line totals rounded half up; PO totals sum the lines. No purchase tax or shipping model is specified.
  Currency comes through catalog's existing public currency function, keeping the dependency graph unchanged.
- Goods may be received after manual send. Each receipt line records a costed inventory movement with
  `reason=receipt`, `ref_type=goods_receipt` and the receipt ID. The cumulative 110% limit is checked
  under the PO row lock. Owner/admin override requires a reason, retained in history and audit.
- Open supply includes approved, sent and partially received POs, excluding draft/cancelled/received.
  Suggestions clamp `inventory.suggested - outstanding` to zero. Inventory's own suggestions are unchanged.
- Inventory exposes `read_reorder_draft` and versioned `mark_reorder_draft_converted`; the latter retains
  its existing `sent_to_purchasing` status. Conversion atomically creates an unnumbered PO, requiring a
  supplier-item price for every draft line, then marks the draft converted. One draft creates at most one PO.
- `purchasing.create_po_from_draft` freezes the supplier name, currency, draft note, line names,
  quantities, costs and totals at preview; execution checks the actual values it will store.
  `purchasing.approve_po` supplies the approved-proposal path and binds execution to the PO version.
- Creating supplier/PO/receipt/conversion POSTs support idempotency keys, serialized per tenant/key.
  Edits, transitions, draft conversion and receipts check versions. All writes are audited.
- Owner/admin: all permissions. Inventory manager: read/write/receive. Operator: read/receive.
  Auditor: read. Write includes supplier maintenance, manual send and cancel; approval and over-receipt
  override are separate owner/admin permissions.
- `/purchasing` has Purchase orders, Suppliers and Reorder suggestions tabs; PO detail includes editable
  draft lines, totals, confirmation dialogs, receipt entry and receipt history. Money inputs use ordinary
  decimal currency values. The optional inventory-item open-PO display is deferred.
- The base seed adds Example Parts Supply and Example Tyre Co. with example.com contacts, brake-pad/tube
  supplier items, and an approved five-pair brake-pad PO (PO-2026-0001, no expected delivery date) for
  Example Cycle Works. The seed has grown since: see "As built — procurement demo data" at the end of this card.
- Browser workflow and accessibility checks are supplied but remain unverified here: Chromium cannot
  launch in the sandbox (`MachPortRendezvousServer` bootstrap permission denied).
- Verification: 313 backend tests pass; the purchasing/inventory/architecture subset passes 46;
  160 frontend tests pass. Ruff lint/format, mypy, TypeScript, frontend lint, contract generation,
  license checks and secret scanning pass. `make check` stops at uv cache access in this sandbox;
  deployment static checks were not reached. E2e: two login setups fail to launch Chromium and
  the remaining 17 tests do not run. Generated browser reports were removed.

- Receipt recovery hardening: the dialog freezes one UUID idempotency key, version and quantities
  per attempt. An unknown response can be retried unchanged. Unknown outcomes and conflicts
  require reloading and reviewing recorded deliveries before a new attempt starts with blank
  quantities and a new key. Failed receipt reloads preserve the original attempt; failed supplier
  reloads keep conflict recovery open and report the error before accepting any cached data.
- Recovery follow-up verification: 18 purchasing tests, 314 full backend tests and 166 frontend
  tests pass. Ruff lint/format, mypy, TypeScript, frontend lint and regenerated contracts pass.
  `make check` remains blocked by sandbox permission denial on uv's cache directory.


## As built — PQ-01 procurement groundwork (2026-11-03)

- Five business-scoped quote tables with forced RLS, composite tenant references and a reversible
  migration after `20261031_1100_invoice_reminders`. Request states are draft/sent/closed/cancelled;
  invites track invited/responded/declined/expired and send/response/followup times. Supplier replies
  replace the current quote and increment its version, retaining form/staff/assistant_extracted provenance.
- Four approval executors and staff proposal routes implement request sending, manual quote entry,
  PO creation and supplier followup. Notifications outbox templates `quote_request` and
  `supplier_followup` are now a declared purchasing dependency in code and architecture docs.
  Sending validates every supplier email and enqueues exactly one invite per supplier. Followup
  timestamps mean enqueued; they do not claim successful email delivery.
- Public `GET/POST /api/v1/public/quotes/{token}` use HMAC quote1 capabilities bound to business,
  request and supplier, checked against invite hashes. Expiry is the midnight after the complete
  business-local reply_by + 7 date. The form can replace a quote or decline, with persistent per-IP
  and per-invite rate limits. It exposes no competing offers or supplier addresses. The `/q/{token}`
  screen is PQ-02; this card supplies the backend only.
- Comparison applies all frozen eligibility gates before ranking: coverage, business currency,
  validity, minimum/available quantities and positive total. It returns explicit reasons and all
  scoring components. Score is 60% relative price, 25% delivery and 15% last-ten-PO reliability,
  rounded to three decimals, breaking ties by total and lead time. Reliability uses final goods
  receipt dates against expected delivery in the business timezone, defaulting to 0.8 below three
  completions. Unknown expected dates count as late once sufficient history exists.
- Accepting a current eligible quote creates a draft PO, marks the quote accepted and closes the
  request atomically. It cannot bypass normal PO approval or create multiple POs for one request.
  Sending now records sent_at and expected_delivery_date from the slowest supplier-item lead time
  (zero for a missing lead); quote-derived POs use the accepted quote's offered lead. The expected
  date remains version-editable until receipt. Existing POs without an expected date are unknown,
  rather than inferred late. The late-PO read lists only outstanding sent/partially received orders
  using business-local dates.
- Daily per-business `purchasing-expire-quote-invites` expires unanswered invitations after their
  grace period. Public scan reads return sent requests due for comparison without a PO, and overdue
  unanswered live invites; invites and POs record last_followup_at. `purchasing.quote_received`
  carries only request/supplier IDs plus declined/all_answered; declined and expired count as answered.
- Export includes all five tables but excludes invite token hashes and outbox quote links. Customer
  erasure redacts identifiers in request messages, quote notes and copied PO notes; supplier master
  contact data remains supplier data. The module README documents routes, payloads and edge decisions.
- No AI code, prompts, models, inbound email parsing or supplier login portal is included. Verification
  results and owner-run checks are reported in the task response; tracking files remain owner-managed.

- PQ-01 review hardening: platform request context and unexpected-error logging mask quote capability
  segments for `/api/v1/public/quotes/{token}` and `/q/{token}`. Scoring retains exact rational price,
  lead and reliability components through final half-up rounding. Conversion proposals apply the same
  line/aggregate PO amount limits as execution, rejecting impossible amounts before approval.
- Purchasing and notifications register erasure-link discovery, including customer mentions existing
  only in supplier followup bodies. Linked purchasing outbox copies lose their message bodies and
  become blocked for future delivery claims/retries, while supplier recipients and capability variables
  are preserved. Credential variables remain excluded from exports; unrelated requests and tenants
  remain untouched. In-flight external sends cannot be recalled.


## As built — procurement demo data in the seed (2026-11-03)

`backend/app/seed.py` gives Example Cycle Works the data the quote comparison, the late-order list and the supplier
follow-up need. It is created through the purchasing services (real numbers, tokens, receipts, stock movements and audit
events) and is not part of `data/fixtures.json`. Every date is counted from the business-local day of the seed run (**S**),
so "late" stays true whenever it runs. The full table of prices and scores is in `data/README.md` ("Procurement and payment
showcase"); `docs/roadmap/ai/integration-map.md` §G lists the facts the AI evals may rely on and
`backend/tests/integration/test_seed.py` asserts them.

- **Four suppliers**, all with example.com contacts: Example Parts Supply (parts@) and Example Tyre Co. (tyres@, inner tubes;
  the original two), plus Example Brake Direct (brakes@) and Example Cycle Wholesale (wholesale@, which also sells Cable set).
  **Three sell "Brake pads (pair)"**, none of them the best on every count: Example Cycle Wholesale $10.50 / 16 days (cheapest),
  Example Parts Supply $12.00 / 3 days, Example Brake Direct $14.00 / 2 days (dearest and fastest).
- **Purchase-order history**: eight POs in all. PO-2026-0001 is the original approved order (Parts Supply, five pairs, no
  expected date, so never late). PO-2026-0002 to -0007 are completed orders of two pairs each, ordered 66 to 19 days before S
  with goods receipts (three from Cycle Wholesale, on time once and 1 and 4 days late; three from Brake Direct, all on
  time). Each delivery is followed by the same quantity used on brake services, so stock stays at 3 on hand and no reorder
  event is raised. This history is what gives the suppliers their reliability: Cycle Wholesale 0.333, Brake Direct 1.0, and
  Parts Supply the 0.8 default (fewer than three completed orders).
- **One open quote request** for ten pairs, sent on S-3 to the three pad suppliers (reply by S+7, needed by S+11). Example Brake
  Direct (S-2, $14.00, 2 days) and Example Cycle Wholesale (S-1, $10.50, 16 days) have answered through the public form,
  **two of three answers**; Example Parts Supply has not, so the demo can answer for it. The comparison ranks Brake Direct
  (0.850) above Cycle Wholesale (0.811), and a $11.80 / 3-day answer from Parts Supply would rank first (0.904). The three
  invitation e-mails use the normal channel rule (`simulated` without SMTP), so they are delivered by the worker and never
  show as delivery issues.
- **One late purchase order**: PO-2026-0008, five Cable sets from Example Cycle Wholesale ($70.00), approved and sent on S-14,
  expected on S-4 (four days late), status `sent`. It is the row behind the Purchasing "late" filter, the Today card and
  `purchasing.list_late_purchase_orders`.
- Spec change: none. The seed only calls existing services; no table, route or executor was added for it.


## As built — readable approval previews and the staff supplier link (2026-11-03)

- **Previews** of all six purchasing executors (`send_quote_requests`, `record_supplier_quote`, `create_po_from_quote`,
  `send_supplier_followup`, `create_po_from_draft`, `approve_po`) now name items (through `catalog.public.get_items`) and
  show quantities and money as people read them, with the currency: "Brake pads (pair): 10 × 11.80 USD = 118.00 USD", then
  "Total: 118.00 USD". Quote and follow-up previews no longer show item ids, JSON or "minor units". The text lives in
  `previews.py` and uses `app.platform.money.money_text`. `create_po_from_quote` previews the PO with the same
  `prepare_lines` pricing its execution applies, so the preview cannot disagree with the PO that is created.
- **Copy link.** Without SMTP (the demo) a pending supplier's quote page exists only in a simulated e-mail, so staff with
  `purchasing.po.write` can ask for it: `POST /api/v1/purchasing/quote-requests/{id}/invites/{supplier_id}/link` returns
  `{url}` built by `quote_service.quote_link`, the same function as the e-mail's `quote_url`, with `Cache-Control: no-store`.
  Only while the request is `sent` and the invite is `invited` and not expired (409 `purchasing.quote_request_closed` /
  `purchasing.invite_not_unanswered` otherwise). The audit event `purchasing.quote_link.copied` records the request and
  supplier ids, never the link, and nothing logs it.
- **Expiry is derived, not stored.** The invite stores only the token hash. The e-mail's expiry follows from the request's
  `reply_by` and the business time zone (`expiry`), so the same token is rebuilt from stored data and compared with the
  stored hash. If it differs (the secret or the time zone changed after sending) the answer is 409
  `purchasing.quote_link_unavailable` rather than a guessed link; send a follow-up instead.
- **UI**: *Copy link* beside *Enter quote* and *Send follow-up* on each supplier that has not answered. It copies to the
  clipboard and toasts; only if the browser refuses does a dialog show the link for copying by hand.
- Spec change: one staff route and one audit action were added; no table or migration. The OpenAPI contract and
  `schema.d.ts` were regenerated (`make gen-api`).
