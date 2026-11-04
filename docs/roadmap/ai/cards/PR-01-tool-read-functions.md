# PR-01 · Read functions and executors the AI tools need

Kind **PRE** (ordinary ERP features, no AI) · Tier B · Size M · Depends on: — · Blocks: AI-06 (some tools), AI-11 (U6), AI-15 (U10)

## Goal
Give the future AI tools stable, typed `public.py` functions for three things that today only exist inside API
handlers or not at all, and give staff a manual "send payment reminder" action. Every piece is useful without AI.

## Context pack (read in this order; nothing else)
1. `docs/roadmap/ai/integration-map.md` §B rows `today_summary`, `list_overdue_invoices`; §D row `propose_payment_reminder`.
2. `docs/roadmap/04-repository-and-conventions.md` section "Code patterns" (how service, public, api, executors look).
3. `backend/app/modules/dashboard/api.py` (the `today` endpoint computes the Today data inline).
4. `backend/app/modules/sales/` — `README.md`, `models.py` (Invoice), `public.py`, `executors.py`, `service.py` headers only.
5. `backend/app/modules/notifications/public.py` (`enqueue_message`) and `templates.py`.
6. `backend/app/modules/dependencies.py` (allowed imports).

## You build
1. **Dashboard:** move the Today computation from `dashboard/api.py` into `dashboard/service.py`
   (`today_summary(db, ctx) -> TodaySummary`), keep the API response identical (`TodayOut` may simply be
   `TodaySummary`), and add `dashboard/public.py` exporting `today_summary` and `TodaySummary`.
2. **Overdue invoices:** `sales.public.list_overdue_invoices(db, ctx, *, as_of: date | None = None, limit: int = 50)
   -> list[OverdueInvoice]` — issued (not paid, not void) invoices of kind `invoice` with `due_date < as_of`
   (as_of = today in the business time zone), oldest first. `OverdueInvoice`: `id, number, customer_id, bill_to_name,
   bill_to_email, due_date, days_overdue, total_minor, currency, version, last_reminder_at`.
3. **Payment reminder executor** `sales.send_payment_reminder`, payload
   `{invoice_id, invoice_version, body (1–2000)}`, approve permission `sales.document.write`; validates the invoice is
   still overdue and has a bill-to e-mail; run: enqueue an outbox e-mail (new template `payment_reminder`: subject
   "Payment reminder: invoice {{number}}", body = the text + amount and due date), set `invoices.last_reminder_at`
   (new nullable column, migration after the current head), audit event `sales.payment_reminder.sent`.
4. **Manual path:** `POST /api/v1/sales/invoices/{id}/reminders {body, version}` for staff with `sales.document.write`
   (creates a staff proposal for that executor, like other staff proposals) and an "Overdue" filter + "Send reminder"
   dialog on the Sales → Invoices screen (frontend, reuse existing dialogs; text typed by staff).

## Tests (write first)
- `test_today_summary_matches_today_endpoint` (same data as before the move).
- `test_list_overdue_invoices_only_issued_unpaid_past_due_in_business_timezone` (paid, void, credit notes, future due
  dates and other businesses excluded; DST-safe "today").
- `test_payment_reminder_executor_sends_email_once_and_records_time` (version check, idempotent on retry, audit).
- `test_payment_reminder_refused_without_email_or_when_paid`.
- Frontend: overdue filter shows only overdue invoices; reminder dialog posts the typed text.

## Done when
`make check` passes; `make gen-api` regenerated types; e2e still passes (`make e2e`). Update `docs/roadmap/modules/M11-dashboard.md`
and `docs/roadmap/modules/M16-sales.md` "As built" and the module READMEs. Record the work in `PRE_EVENT_WORK.md`.

## Out of scope
Any AI code, automatic reminders, payment processing.
