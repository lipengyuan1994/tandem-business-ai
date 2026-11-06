# purchasing (M17)

Buy stock, solicit and compare supplier quotes, approve POs, deliver them and receive goods.
Business services are deterministic; model orchestration belongs to AI.
Spec: [M17](../../../../docs/roadmap/modules/M17-purchasing.md),
[A1 contract](../../../../docs/roadmap/ai/contracts.md#p10-a1-confirm-in-chat-po-delivery-send-delay).

Tables: `suppliers`, `supplier_items`, `purchase_order_sequences`, `purchase_orders`,
`purchase_order_lines`, `goods_receipts`, `goods_receipt_lines`, `quote_requests`,
`quote_request_lines`, `quote_request_suppliers`, `supplier_quotes`, `supplier_quote_lines`.
Forced business RLS and composite tenant references throughout; receipts are append-only.
Exports omit capability hashes/URLs. Erasure links procurement records and their outbound copies,
redacts customer-bearing text and blocks further delivery; supplier master contacts remain.

Permissions: `purchasing.po.read|write|approve|receive`, `purchasing.receipt.override`.
Owner/admin have all; inventory manager read/write/receive; operator read/receive; auditor read.
Supplier writes, manual send/cancel and follow-up approval use `.write`; PO decisions use `.approve`.
Suppliers have `active=true` by default, editable through the existing versioned supplier API.

Executors (all use the existing proposal/decision/job path):
- `create_po_from_draft` (`.approve`): freezes supplier/currency/names/quantities/costs and atomically
  creates a draft PO and converts the reorder draft; stale snapshots fail. Bounded `requested_lines`
  may override source-draft quantities for staff; actual quantities/costs/total stay snapshot-bound.
- `approve_po` (`.approve`): version check, transactional gap-free business-local PO number/date.
- `send_po` (`.approve`, A1): `purchase_order_id, version`; shares manual `send` state rules,
  rejects prior sent POs, inactive suppliers and missing email. One `purchase_order` outbox email
  and the sent transition commit together. Text/escaped HTML contain business/PO/date, priced lines,
  total/currency and default delivery location; internal notes are excluded. Reply-To uses org email.
  Second person: total >50,000 minor units, other currency, or no prior sent PO for this supplier.
  Send/create expose money callbacks for chat's per-order/daily limits; draft/quote identity prevents
  charging the same order twice across creation and sending. Stopping mail does not revert a PO.
- `send_quote_requests` (`.approve`): validates 1–5 supplier emails, creates sent request/invites,
  queues one `quote_request` email each; execution key serializes replay with stable request ID.
- `record_supplier_quote` (`.write`): replaces an invited supplier quote, increments version and
  marks responded; staff/assistant_extracted sources. The source label grants no extra authority.
- `create_po_from_quote` (`.approve`): current version, eligible received quote; applies line/total
  limits, creates an unnumbered draft PO, accepts quote and closes request. One PO per request.
- `send_supplier_followup` (`.write`): queues mail for unanswered live invite or sent/partially
  received PO and records last_followup_at, meaning queued rather than delivered.

All ordinary supplier mail uses `notifications.email_channel()`; no SMTP means simulated delivery.
AI-origin proposals get the configured send delay even when a person decides. Manual staff mail
has no delay. Delivery is independently visible through notifications/proposal detail.

Public reads: `open_quantities`, `list_suppliers_for_item`, `get_quote_request`, `compare_quotes`,
`find_open_quote_request`, `list_late_purchase_orders`, `list_quote_requests_due`,
`list_unanswered_invites`, `get_late_purchase_order`, `get_unanswered_invite` (scoped eligibility).
A1: `next_order_step(db,ctx,*,purchase_order_id=None,draft_id=None,supplier_id=None)` requires PO
read and returns one action/payload/title (create, approve, send), never executes. The AI tool wraps
it in a proposal. `order_quantities(db,ctx,po_id)` checks existing lines for conflicting requests.
`awaiting_supplier_items(db,ctx,*,limit,cursor)` requires proposal read and supplies
pending sent quote invitations with reply_by for the unified Work queue; contacts/URLs are omitted.
Staff `/api/v1/purchasing`: supplier/item CRUD; versioned PO CRUD/approve/send/cancel, conversion,
receipts; quote-request POST/GET/detail/quotes/comparison; PO-from-quote; supplier-followups;
late-purchase-orders; items/{id}/suppliers; quote-requests/{id}/invites/{supplier_id}/link.
Creating staff requests use Idempotency-Key; proposal previews show named quantities and money.
Copy-link requires write, sent request, unanswered/unexpired invite and matching token hash;
returns Cache-Control:no-store and audits identifiers only. Reuses the emailed URL construction.

Public `/api/v1/public/quotes/{token}` GET/POST: signed `quote1` capability binds business/request/
supplier/expiry. Only hash is stored. Local reply_by+7 days remains valid through midnight (DST
aware). Token rebuilt from immutable invite data; no guessed replacement after secret/timezone change.
GET returns business, requested lines, message/dates; POST supplies prices/availability/lead/minimum/
validity/notes or declines. Business currency, no competitors/contacts. Closed/expired is refused.
Limits: GET 120/IP/min, 60/invite/min; POST 60/IP/hour, 10/invite/min. Logs mask capability paths.

Comparison excludes incomplete/other_currency/expired/quantity/invalid_total offers. Quantities are
requested values; line totals round half up. Exact rational score: 0.6×cheapest/total + 0.25×lead +
0.15×reliability, final half-up to three decimals; ties lower total, shorter lead, UUID. Reliability
uses last ten completed POs by final receipt, default 0.8 below three completions; absent promised
dates count late. Lead=1 by needed_by, otherwise max(0,1−days_late/14). Components remain visible.

Events: `purchasing.quote_received` (request/supplier/declined/all_answered). Daily per-business
`purchasing.expire_quote_invites` expires invitations after reply_by+7 days; declined/expired count
as answered before the sweep too. Sending calculates expected date from slowest supplier-item lead
(or the accepted quote's lead). Only active stock products; draft-only edits except expected date.
Late means date < business-local today; absent date is unknown. Receipts require sent/partially
received; >110% needs owner/admin override reason. Cancellation preserves receipts/numbering.

Absent requested_lines stays absent on serialization, preserving legacy pending proposal hashes.  P10-D: public.dictation_names(db, ctx, entity_ids) returns bounded permission/business-scoped names only.
