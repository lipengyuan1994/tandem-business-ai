# Sales screens

Sales lists and document details use generated API types and permission-gated actions.

In **Sales → Invoices**, choose **Overdue** in the Status filter. The server applies business-local date, issued
status and ordinary invoice kind before cursor pagination. Paid/void invoices and credit notes are excluded.
Staff with `sales.document.write` can choose **Send reminder**, type 1–2000 characters and create a reminder proposal.
The dialog posts the exact text and displayed invoice version, then links to **Review reminder proposal** in Approvals.
The amount and due date are appended by the server. No email is sent before approval. Missing bill-to email disables
the action; stale invoices and other server errors remain visible in the dialog with the text preserved.

This uses the existing proposal-create/approval policy and outbox worker, with no automatic reminders or model calls.
The sales action tests wait for mutation invalidation and destination queries to finish, including a deliberately
deferred destination GET, before test teardown can replace MSW handlers.

**Payments and receivables.** On an issued invoice, staff with `sales.invoice.mark_paid` choose **Record payment** to
enter a full or part payment (amount prefilled with the open balance, date in the business time zone, method and an
optional reference). The amount may not exceed the balance. The invoice shows Paid, Balance due and the payment history;
admins (`sales.invoice.credit`) can void a payment entered in error after confirming. Invoice lists show Balance due and
a Partially paid badge. **Sales → Receivables** shows open balances per customer by days past due, one table per
currency, for today or a chosen date.
