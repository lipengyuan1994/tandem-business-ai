# purchasing (M17)

Buy stock, solicit supplier quotes, compare deterministic scores, approve POs and receive goods.
This is ordinary PRE ERP work: no inference, prompts, extraction or automatic purchasing.

Tables: `suppliers`, `supplier_items`, `purchase_order_sequences`, `purchase_orders`,
`purchase_order_lines`, `goods_receipts`, `goods_receipt_lines`, `quote_requests`,
`quote_request_lines`, `quote_request_suppliers`, `supplier_quotes`, `supplier_quote_lines`.
All use forced business RLS, composite tenant references and business export. Receipt tables
are append-only. Quote capability hashes and outbox quote URLs are excluded from exports.
Customer erasure redacts identifiers in request messages, quote notes and PO notes. Registry linked IDs
connect those records with their request/followup outbox copies, including mentions existing only in a
followup body. Linked procurement message bodies are erased and further delivery claims/retries blocked;
supplier addresses and capability URLs remain intact. Unrelated requests and supplier master contacts
are preserved. Audits/events contain identifiers and statuses,
never quote tokens, supplier addresses or free text.

Permissions: `purchasing.po.read`, `.write`, `.approve`, `.receive`,
`purchasing.receipt.override`. Owner/admin have all; inventory manager has read/write/receive;
operator has read/receive; auditor has read. Write includes supplier editing, send and cancel.
New staff writes create proposals; the executor's permission controls who can approve them.

Executors:
- `purchasing.create_po_from_draft`: freezes supplier, currency, names, quantities and costs;
  atomically creates a draft PO and marks the inventory draft converted.
- `purchasing.approve_po`: binds approval to the PO version and assigns its gap-free number.
- `purchasing.send_quote_requests` (`.approve`): creates a sent request and 1–5 invites;
  validates supplier email and queues one `quote_request` email per supplier. The execution
  idempotency key serializes replay and derives a stable request ID.
  Supplier e-mails (`quote_request`, `supplier_followup`) take their channel from `notifications.email_channel()`:
  `simulated` (delivered by the worker, labelled Simulated, nothing sent) until `SMTP_URL` is set, then `email`.
- `purchasing.record_supplier_quote` (`.write`): replaces the invited supplier's quote,
  increments its version and marks the invite responded. Sources: staff or assistant_extracted
  (the latter is only a provenance label here; no extraction exists).
- `purchasing.create_po_from_quote` (`.approve`): requires the current version and an eligible
  received quote; applies existing PO line and aggregate amount limits at preview and execution,
  then creates an unnumbered **draft** PO, accepts the quote and closes the request.
  The PO still needs normal approval before send. A request produces at most one PO.
- `purchasing.send_supplier_followup` (`.write`): queues a `supplier_followup` email about an
  unanswered live invitation or sent/partially received PO, then records `last_followup_at`.
  This timestamp means queued, not confirmed delivery; use notifications for delivery state.

Staff routes below `/api/v1/purchasing`:

| Method | Path | Result |
|---|---|---|
| POST | `/quote-requests` | proposal to create and send request |
| GET | `/quote-requests` | cursor page; optional status |
| GET | `/quote-requests/{id}` | request, named lines, invites and quotes |
| POST | `/quote-requests/{id}/quotes` | proposal to record staff quote; body includes matching quote_request_id and source=staff |
| GET | `/quote-requests/{id}/comparison` | ranked and ineligible quotes |
| POST | `/purchase-orders/from-quote` | proposal with quote_id, quote_version |
| POST | `/supplier-followups` | proposal with supplier_id, one about ID, body |
| POST | `/quote-requests/{id}/invites/{supplier_id}/link` | `{url}`: the signed page of a supplier who has not answered (below) |
| GET | `/late-purchase-orders` | sent/partially received open lines; optional as_of |
| GET | `/items/{item_id}/suppliers` | prices, lead times and reliability |

Staff copy link (`purchasing.po.write`): `POST .../invites/{supplier_id}/link` returns the supplier's signed page, built
by `quote_service.quote_link` exactly like the e-mail's `quote_url`, with `Cache-Control: no-store`. It is refused with 409
unless the request is `sent` (`purchasing.quote_request_closed`) and the invite is `invited` and not past its expiry
(`purchasing.invite_not_unanswered`). The invite stores only the token hash and the e-mail's expiry is not stored: it is
derived from the request's `reply_by` and the business time zone (`expiry`). The token is rebuilt from those and must
match the stored hash; if it does not (the secret or the time zone changed since sending) the answer is 409
`purchasing.quote_link_unavailable`, never a guessed link. Each success audits `purchasing.quote_link.copied` (request
and supplier ids only, never the link) and nothing logs it.

Approval previews (`previews.py`) show item names, plain quantities and money in normal units with the currency, one line
per item ("Brake pads (pair): 10 × 11.80 USD = 118.00 USD") and a total; never ids or minor units.

Creating staff POSTs accept `Idempotency-Key`, returning the same proposal on replay. Existing
supplier/PO/receipt/conversion APIs retain their normal idempotency and version checks.
Quote requests are created when the approved send proposal executes, not when it is proposed.
Dates and duplicate lines/suppliers are validated; reply_by cannot follow needed_by or precede today.

Public form API (no login): `GET` and `POST /api/v1/public/quotes/{token}`. GET returns only business
name/currency, requested lines, message and dates, never competitors' quotes or supplier contacts.
POST accepts lines with item_id, unit_price_minor and optional quantity_available, lead_time_days,
optional min_quantity, valid_until and notes (≤500); currency is the business default. Alternatively
send only `{"declined":true}`. Each response replaces that supplier's quote; a decline rejects its
previous quote. Closed requests and expired capabilities cannot be submitted. Incomplete replies
can be recorded, but cannot rank or become a PO. An unrequested item is rejected.

The emailed link is `https://<APP_BASE_URL host>/q/<token>` (the screen is PQ-02).
Tokens use HMAC-SHA256, purpose `quote1`, binding business/request/supplier and an absolute expiry;
only their SHA256 hash is stored on the invite. The complete local date reply_by + 7 days remains
valid, expiring at the next local midnight (DST-aware). Reads allow 120/IP/minute and 60/invite/minute;
posts allow 60/IP/hour and 10/invite/minute, using persistent platform rate limiting. Platform request
logging masks capability segments for both /api/v1/public/quotes/{token} and /q/{token}, including
unexpected-error logs and bound request context.

Comparison uses business-local today. Quotes must cover every requested line, match the business
currency, remain valid today, satisfy minimum quantity for **each** requested line and availability
for each line, and have a positive total. Ineligible results list all applicable reasons: incomplete,
other_currency, expired, quantity, invalid_total. Declined/rejected offers remain in request history
and are excluded from comparison. Requested quantities determine totals; line totals round half up.
Eligible scores are `0.6 × cheapest/total + 0.25 × lead + 0.15 × reliability`, rounded half up to
three decimals. All components use exact rational arithmetic until this final rounding; only response
components become floats (267/400 price and 2/3 reliability with on-time delivery score 0.751).
Lead is 1 for arrival by needed_by, otherwise max(0, 1 − days_late/14). Reliability
uses the last ten completed POs ordered by their final goods receipt, with receipt dates in the
business timezone; absent promised dates count as late. Fewer than three completions defaults to
0.8 (supplier-option on_time_rate is null). Ties use lower total, then shorter lead time, then UUID
for a stable final order. The response exposes every component, total, arrival date and days late.

Public reads additionally expose `get_quote_request`, `list_suppliers_for_item`, `compare_quotes`,
`find_open_quote_request(db, ctx, item_id) -> UUID | None` (the first sent request containing the tenant's item),
`list_late_purchase_orders`, `list_quote_requests_due`, `list_unanswered_invites` and their DTOs.
Due requests are sent, have no linked PO and either passed reply_by or have all invites answered.
Unanswered invites are overdue, live invitations on sent requests; they carry followup timestamps.
Scoped point reads for per-target AI jobs/tools: `get_late_purchase_order(db, ctx, purchase_order_id, *, as_of=None)`
returns `LatePO | None`; `get_unanswered_invite(db, ctx, quote_request_id, supplier_id, *, as_of=None)` returns
`UnansweredInvite | None`. Both require `purchasing.po.read`, apply the list reads' eligibility rules, and return
None for missing, foreign-business or no-longer-eligible targets without loading other orders/invites.
`open_quantities` still returns open supply; inventory does not depend on purchasing.

Daily per-business cron `purchasing-expire-quote-invites` runs `purchasing.expire_quote_invites`,
marking invites expired after reply_by + 7 days. `purchasing.quote_received` is published for every
recorded or declined response, with quote_request_id, supplier_id, declined, all_answered. Declined
and expired invitations count as answered even before the daily expiry sweep has run.

PO numbering is transactional and business-local (`PO-2026-0001` on approval). Only active
stock-tracked products can be ordered. Sending records UTC sent_at and computes expected delivery
from the slowest matching supplier-item lead time (missing lead time defaults to zero). A PO from
an accepted quote uses that quote's offered lead time. The date can be edited with the current
version until received; other PO edits remain draft-only. Sending always recalculates the date.
Late means expected_delivery_date < local today; older POs without dates remain unknown.
Receipts require sent/partially received status; cumulative quantities above 110% require an
owner/admin override reason. Cancellation preserves recorded deliveries and numbering.
