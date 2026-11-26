# Review legacy refund dates before relying on historical aging

The current checks reject a refund if it would leave a document with more cash refunded than paid as of any refund
date. They also block voiding a payment that a refund still needs. Older records are **not** rewritten on upgrade.
The aging report avoids adding a refund before its associated credit, and caps refund add-backs at cash paid as of
the report date. These display protections do not fix the underlying cash ledger or certify a closed period.

1. With the owner's approval, work from an isolated restore or an authorized read-only export for the affected
   business. Do not copy customer details, bank information or credentials into tickets or output.
2. On the sales side, group live `invoice_payments` and `invoice_refunds` by business and invoice, using payment and
   refund `paid_on`. On the buying side, group live `supplier_bill_payments` and the refund portions of live
   `supplier_credits` by business and bill, using payment `paid_on` and refund `refund_received_on`. Ignore voided
   payments and supplier credits. Exclude credits with no cash refund. Use document currency and amounts in minor units.
3. Sum payments and subtract refunds by effective day, then calculate a running balance within each document. A
   negative balance on a refund day calls for review. A same-day payment qualifies; a legitimate deposit before the
   invoice or bill date is not an error. Do not substitute the row creation date, credit date or bank clearing date
   for the date cash moved. Check the matching credit and source payment and refund records.
4. Have the bookkeeper decide whether the date is wrong, a payment is missing, or the refund needs a separate
   reconciliation. Use supported correction operations only after approval. If the necessary correction is not
   supported, escalate instead of editing database rows, backdating cash that did not move, or reissuing a refund.
   QuickBooks refund and supplier-credit reconciliation remains a separate manual step.
5. On the isolated copy, rerun the check and verify aging before and after the affected dates against the corrected
   evidence. Keep a minimal record of decisions and obtain bookkeeper sign-off before relying on an affected close
   or applying a correction to live books.

This is an inspection and triage procedure, not an automated data migration. No live ledger was checked in the
November 25 review.
