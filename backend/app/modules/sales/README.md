# sales · M16

Commercial documents with snapshots, integer-minor-unit totals and transactional numbering.
No payment processing, general ledger, external delivery or AI code.

Tables: document_sequences, quotes, quote_lines, sales_orders, sales_order_lines,
invoices (invoice / credit_note), invoice_lines. Every table has business RLS and composite FKs.

Public API (`db, ctx` first; caller owns the transaction):
- `create(db, ctx, family, DocumentCreate) -> Document`
- `update(db, ctx, family, id, DocumentUpdate) -> Document`
- `transition(db, ctx, family, id, version, action) -> Document`
- `convert_quote(db, ctx, id, version) -> Document`
- `invoice_order(db, ctx, id, version) -> Document`
- `from_appointment(db, ctx, appointment_id) -> Document`
- `credit(db, ctx, id, version) -> Document`
- `detail(db, ctx, family, row) -> DocumentOut`
- DTOs: DocumentCreate, DocumentUpdate, DocumentOut, LineIn.
- `list_overdue_invoices(db, ctx, *, as_of: date | None = None, limit: int = 50) -> list[OverdueInvoice]`
  requires `sales.document.read`; returns issued ordinary invoices due before the business-local date, oldest due
  date first (ID breaks ties). An explicit `as_of` overrides today. DTOs include the bill-to email, version,
  days overdue and nullable `last_reminder_at`. Zero/negative limits return no rows.

Manual payment follow-up (PR-01): `/invoices?overdue=true` filters before cursor pagination using the business-local
date; status/customer filters still apply. `POST /invoices/{id}/reminders {body, version}` requires
`sales.document.write` and the existing staff proposal-create permission, and returns a proposal reference
(`id`, `status`, `current_revision_id`) with HTTP 201. It creates a staff proposal for
`sales.send_payment_reminder`; it does not send immediately. Review and approve through the existing Approvals UI.
The invoice must still be issued, ordinary, past due, version-current and have a bill-to email at both proposal
creation and execution. Text is 1–2000 characters. The executor's approval permission is `sales.document.write`.
Execution locks the invoice and atomically queues one `payment_reminder` email, records `last_reminder_at`, increments
the invoice version and audits `sales.payment_reminder.sent`. The timestamp means queued for delivery, not delivered.
The email uses the channel from `notifications.email_channel()`: `simulated` (delivered by the worker, labelled
Simulated, nothing sent) until `SMTP_URL` is set, then the real `email` channel.
Approvals' durable execution record handles retries; the outbox uses the execution idempotency key. A rolled-back
execution leaves no reminder timestamp, outbox email or audit event. Concurrent proposals for the same invoice version
cannot both execute. Delivery uses the existing outbox worker; no automatic reminder job or payment processing exists.
Amounts use each document currency's ISO 4217 exponent; due dates are ISO dates.

API: `/api/v1/sales/{quotes,orders,invoices}` (cursor lists, get, create, PATCH).
POST transitions require `{version}`. Quote expiry is an explicit `/expire` action.
Conversions require accepted quotes or confirmed/fulfilled orders. Each source converts once.
Completed appointment invoices contain the service only: inventory exposes no consumed-parts read.
`/invoices/export.csv?from=YYYY-MM-DD&to=YYYY-MM-DD` includes both dates and non-draft tax documents.
CSV values use the document currency's ISO 4217 minor-unit exponent (0/2/3 decimal places);
credit notes have positive amounts and a kind. Credit notes cannot be marked paid.
Every document-creating POST accepts Idempotency-Key via the platform response cache (same body replays
201; changed body or operation conflicts). This includes conversions, appointment invoices and credits.

Numbers QUO/SO/INV/CN are assigned on send/confirm/issue, per business, business-local year and type. Issue dates use the business timezone, including credit notes.
Sequence insert-on-conflict plus SELECT FOR UPDATE runs in the status-change transaction.
Per-line subtotal and tax use Decimal ROUND_HALF_UP; totals sum stored line values.
Catalog names and prices are copied. Every new line snapshots org's sales_tax_rate (default 0),
unless an explicit tax_rate is supplied, including zero. Draft replacement lines allow rate edits;
notes-only edits and conversions preserve the stored rates. Prices are integer minor units; quantities have 3 decimals.
Catalog `each` items require whole units. Draft line replacement snapshots the current catalog values.
Currency comes from catalog's read-only default-currency helper (which reads the org default).
CRM name/email are snapshotted; CRM has no postal address, so creation accepts an optional address.

Permissions: sales.document.read/write/issue, sales.invoice.mark_paid/credit, sales.proposal.approve.
Owner/admin: all. Operator: read, draft writes, transitions, mark paid. Auditor: read.
Credit notes and proposal approval require admin/owner. Service writes also check permissions.
Executors: sales.create_quote, sales.confirm_order, sales.send_payment_reminder (describe / validate / run).
Quote validation captures priced lines, currency and totals in the immutable proposal payload before hashing.
Preview uses that snapshot. Execution recomputes once and returns 409 sales.price_changed on a mismatch,
then persists the same checked values. Legacy proposals without pricing need a new revision.
Omitted tax remains omitted through payload serialization; explicit zero remains an override.
Published: sales.document_changed, identifier/status/version payload only. No subscriptions or jobs.
All document mutations record audit events in the caller's transaction.

Lifecycle: export all seven tables; link all customer document IDs. Erasure clears bill-to snapshots
and notes on quotes, orders and draft invoices. Issued, paid and void invoices and credit notes keep
their snapshots for legal tax-document retention. Issued documents reject edits.

The exact default tax lives in business_settings.sales_tax_rate (numeric(6,4)) and is exposed by
org's versioned settings API and public get_sales_tax_rate. Sales declares org as a dependency.
Order fulfilment calls the existing
inventory consume_reservations API. The inventory docs promise consumption movements without describing
sales_order separately; the sales flow test asserts M16's sale requirement. Its actual behavior is unverified.
Any needed inventory implementation change is outside scope and awaits owner approval.
PR-01 local verification: the full backend suite passed (588 tests) against localhost:5433, including the
migration downgrade/upgrade round trip, overdue timezone/tenant exclusions, reminder approval execution,
retry deduplication and transaction rollback. Root `make check` and dev-stack e2e remain owner-run checks.
