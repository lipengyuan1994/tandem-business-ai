# M16 · sales (v1)

| Field | Value |
|---|---|
| Release / phase | v1 · P8 |
| Tier / kind | B (A for numbering and totals) |
| Depends on | platform, org, crm, catalog, inventory, scheduling, approvals, notifications |
| Owns tables | `document_sequences`, `quotes`, `quote_lines`, `sales_orders`, `sales_order_lines`, `invoices`, `invoice_lines` |

## Purpose
Commercial documents: quote → sales order → invoice. **No payment processing and no general ledger.** Invoices are documents with numbers and totals that can be exported to accounting software (CSV, v1). AI may propose quotes (`sales.create_quote`) after v1.

## Key rules
1. Document numbers are gap-free per business and type and year (`INV-2026-0001`) using `document_sequences` with `SELECT … FOR UPDATE`. Numbers are assigned only on issue, not on draft.
2. Lines copy `name` and `unit_price_minor` from the catalog at creation. Each new line copies org's
   `sales_tax_rate` (default 0) unless the request supplies an explicit `tax_rate`, including zero. Tax rates can
   be edited on draft lines. Later catalog or settings changes do not alter stored lines or conversion snapshots.
   (Owner decision in chat, 2026-11-03: the catalog has no per-item tax field.)
3. Totals are computed in integer minor units, with rounding per line (half-up) and stored. Tax is a simple rate per line in v1 (tax engines out of scope).
4. A confirmed sales order reserves stock (`inventory.reserve_stock(ref_type='sales_order')`). Fulfilment consumes reservations with `sale` movements. Cancelling releases them.
5. An issued invoice is immutable. Corrections are credit notes (`invoices.kind = credit_note` referencing the original).
6. Status flows: quote `draft → sent → accepted | declined | expired`; order `draft → confirmed → fulfilled | cancelled`; invoice `draft → issued → paid (manual mark) | void (via credit note)`.

## Executors
`sales.create_quote`, `sales.confirm_order` (AI-proposable after v1).

## Tests
- Number sequence gap-free under concurrency; totals rounding; issued invoice immutability; reservation lifecycle; credit note correctness.

## As built (2026-11-03)

- PR-01 adds `sales.public.list_overdue_invoices` and `OverdueInvoice`: issued ordinary invoices due before the
  business-local day (or explicit `as_of`), oldest due date first, with email, version and reminder timestamp.
- `POST /sales/invoices/{id}/reminders {body, version}` creates a staff proposal for `sales.send_payment_reminder`.
  Approval requires `sales.document.write`; the existing staff proposal-create and self-approval policies apply.
  Validation at creation and locked execution requires a current version, past due date and bill-to email.
  Execution atomically enqueues an email with staff text, formatted amount and due date, updates the nullable
  `invoices.last_reminder_at`, increments version and audits `sales.payment_reminder.sent`. This is a queue timestamp;
  delivery is tracked by the existing outbox. Approved execution retries do not enqueue another email.
- The invoice list supports `overdue=true` before cursor pagination. Sales → Invoices offers an Overdue filter
  and Send reminder dialog with typed text and a link to review the resulting proposal. Migration
  `20261031_1100_invoice_reminders` follows `20261031_1000_platform_prerequisites`. No automatic reminders or AI code.
- PR-01 verification: the full backend suite passed locally against localhost:5433 (588 tests), including migration
  downgrade/upgrade and all reminder regressions. Frontend typecheck/lint passed and five consecutive full Vitest
  runs passed (215 tests each). Root `make check` and dev-stack e2e are left to the owner under the run's instructions.

- Added the seven business-scoped sales tables with RLS, composite foreign keys, document/line snapshots,
  versions, per-line Decimal half-up rounding and stored integer-minor-unit totals.
- Added transactional QUO/SO/INV/CN numbering, draft CRUD, guarded status transitions, quote → order → invoice,
  order stock reservation/release/consumption, full credit notes that void issued originals, manual paid marking,
  and inclusive issue-date accounting CSV export. Conversions are single-use; orders can invoice after confirmation
  or fulfilment. Quote expiry has an explicit action; no automatic expiry job was requested.
- Registered the two approvals executors, permissions, audit/domain events and lifecycle handlers; added the
  synthetic sent quote and issued invoice for Ada Example to the seed, with brake service and one pair of pads.
- Erasure redacts bill-to name/email/address and clears notes on quotes, orders and draft invoices.
  **Issued, paid and void invoices and credit notes retain bill-to snapshots for legal tax-document retention.**
- Scheduling is a declared sales dependency with a read-only appointment lookup. Completed-appointment invoices
  include only the service: inventory's public API cannot retrieve consumed parts (the requested fallback).
- Owner decision: org's settings API now exposes `sales_tax_rate`, stored in a separate `numeric(6,4)` column
  with default zero. A follow-up migration adds it after the sales migration already exercised by the owner.
  New catalog and free-text lines snapshot that default or an explicit per-line rate (including zero), editable
  in drafts. Conversions and notes-only edits retain snapshots. Sales reads it through org/public.py and declares
  org as a dependency. CRM has no postal address; creation accepts an optional address. The currency helper
  still reads the org-default currency through catalog/public.py. No other module internals are imported by sales.
- Order fulfilment uses inventory's existing `consume_reservations`; its documentation promises `consumption`
  without describing `sales_order` separately. The flow test asserts M16's `sale` requirement, but that behavior is
  unverified without database access. No outside-scope inventory implementation change has been made.
- No AI, payment processing, general ledger, PDF rendering, email delivery or new dependency was added.
- The owner reports 51 passed and 6 failed in the first database sales/architecture run outside the sandbox.
  Follow-up fixes address transition autoflush, test sessions expiring after clock advances, and the executor test's
  approver permissions. Database execution of those fixes and the new tax tests remains **unverified** in the sandbox,
  which denies TCP access to localhost:5433; subsequent results are supplied by the owner.
  The section date and migration filename are the owner's requested labels, not a claim about execution time.

## As built — readable approval previews (2026-11-03)

- `sales.create_quote` and `sales.confirm_order` previews show the customer's name, one line per item
  ("Brake service: 1 × 45.00 USD = 45.00 USD", before tax), then Subtotal, Tax and Total in normal units with the
  currency. They no longer show "Total (minor units)". `sales.send_payment_reminder` already showed "Amount due".
- The text comes from `app.platform.money.money_text` / `minor_text`, which moved from `sales.service_creation` to the
  platform so purchasing can use the same helper. No table, route or payload changed.

## As built — partial payments and receivables aging (2026-11-24)

- New table `invoice_payments` (business RLS, composite FK to `invoices`): amount (> 0, minor units), `paid_on` date,
  method (`cash`, `card`, `bank_transfer`, `check`, `other`), optional reference, `voided_at`. `invoices` gains
  `amount_paid_minor` (0 ≤ paid ≤ total, database check), always the sum of the invoice's non-voided payments.
  Migration `20261124_1200_invoice_payments` backfills one `other` payment per existing paid invoice, dated on the
  business-local day it was marked paid, so history, balances and aging agree.
- `POST /sales/invoices/{id}/payments {version, amount_minor, paid_on, method, reference?}` (`sales.invoice.mark_paid`,
  so operators can record money received) applies a full or partial payment to an issued ordinary invoice. Payments
  larger than the open balance are refused with 409 `sales.overpayment` (the body carries `balance_minor`); customer
  credit balances are not built. Future payment dates (business-local) are refused with 422. When the payments reach
  the total the invoice becomes `paid`; document status otherwise stays `issued`, so overdue lists and reminders keep
  working for partly paid invoices. `DocumentOut` adds `amount_paid_minor`, `balance_minor`, `payment_status`
  (`unpaid` / `partially_paid` / `paid`) and `payments` for ordinary invoices.
- `POST /invoices/{id}/mark-paid` now records the open balance as one `other` payment dated today.
- `POST /sales/invoices/{id}/payments/{payment_id}/void {version}` (`sales.invoice.credit`, admin/owner) voids a payment
  entered in error and reopens a paid invoice. Payments are never deleted. Invoices with payments cannot be credited
  (409 `sales.invoice_has_payments`); void the payments first. Refunds and partial credit notes are not built.
- Reminders (manual and AI-proposed) state the open balance as the amount due, not the original total.
- `GET /sales/receivables/aging?as_of=YYYY-MM-DD` (`sales.document.read`, default business-local today): open balances
  of issued ordinary invoices by customer and currency in the buckets not yet due, 1–30, 31–60, 61–90 and over 90 days
  past due, with per-currency totals. Days count from the due date, or the issue date when there is none. It is point in
  time: invoices issued after `as_of` and payments dated after it are left out. Credited (void) invoices are excluded.
- UI: invoice pages show Paid and Balance due, a payment history and a **Record payment** dialog; admins can void a
  payment. Invoice lists show Balance due and a Partially paid badge. **Sales → Receivables** shows the aging report.
- Not built: customer credit balances and refunds, partial credit notes, payment fields in the accounting CSV export,
  aging CSV export and drill-down from aging to invoices.
