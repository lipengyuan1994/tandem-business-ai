# notifications (M10) — module card

Spec: [docs/roadmap/modules/M10-notifications.md](../../../../docs/roadmap/modules/M10-notifications.md)

- Tables: `outbox_messages` (RLS-exempt queue), `delivery_attempts` (append-only), `in_app_notifications`.
- Public: `enqueue_message` (inside the caller's transaction), `email_channel`, `simulated_delivery`, `notify_users`,
  `outbox_status_counts`, `DeliveryStatusChanged`.
- Channel rule: `email_channel()` is `"simulated"` while `SMTP_URL` is empty and `"email"` once it is set. Every ordinary
  e-mail passes it as `channel=` (marketing campaigns, purchasing supplier mail, sales payment reminders), so an install
  without SMTP delivers them as Simulated instead of failing. The channel is stored on the row when it is queued.
  Features that only make sense with real mail (System "Send test e-mail") pass `channel="email"` and refuse without SMTP.
  `with simulated_delivery():` (a context variable) makes `email_channel()` answer `simulated` inside the block whatever
  `SMTP_URL` says, and restores the previous state when the block ends, also on an error. The demo seed wraps its work in
  it: its supplier and customer addresses are example.com, so seeding must never reach a real SMTP provider. Rows are
  created `simulated`; nothing is rewritten afterwards. Other threads and the worker keep the normal rule.
- Adapters: `simulated`, `web_chat` (MVP); `email` uses stdlib SMTP with verified TLS (P8-02); SMS refuses clearly. Templates in `templates.py` (sandboxed).
- SMTP: multipart escaped HTML/text; stable Message-ID, optional public contact Reply-To; marketing one-click unsubscribe headers.
- `payment_reminder` renders subject `Payment reminder: invoice {{number}}` and staff text followed by the amount,
  currency and due date. Sales queues it only through an approved manual proposal, using the existing transactional
  outbox and delivery worker. Queued reminders are not evidence of successful delivery.
- Delivery: 4xx/connection failures retry; 5xx fail; connection loss/timeout after DATA is unknown (manual resolution).
- Jobs: `notifications.deliver`, `notifications.reap_sending` (cron 60 s). Subscribes to `approvals.proposal_created`.

- P8-23: one profile-aware template selector renders minimal regulated appointment confirmations/reminders in text and HTML. Restriction hooks block pending/uncertain sends without deleting their text.
- Durable `outbox_messages.restricted_at` blocks claiming/retries even after late outcomes or a profile switch; a completed in-flight attempt keeps its actual outcome.

`current_delivery_status` holds an outbox read lock through projection commit. Conversation consumers lock
the message before reading this state, matching erasure lock order and ignoring stale event statuses.

Procurement erasure uses registry linked IDs: customer-bearing requests, quotes, POs and supplier
followup bodies link the purchasing records before erasure. Their quote-request/followup outbox bodies
become `[erased]`, preserving supplier recipients and credential variables. Durable restricted_at blocks
new delivery claims/retries; pending, unknown and sending rows become failed. An already in-flight
external send cannot be recalled and retains its actual outcome. Unrelated suppliers' requests and other
businesses remain untouched. Capability variables (including quote_url) remain excluded from exports.

P10-A0 part 2: `in_app.py` owns in-app reads/writes and the proposal-created subscriber (re-exported by service).
It uses approvals' public notification-state reader: pending proposals request approval, successful shadow checks
explain shadow mode, non-pending `act_notify` policy approvals send `ai_acted` to the same approvers, and `act`/other
non-pending proposals send no approval request. Notifications include a body in the API and panel. Historical
rule versions retain their original notification level after edits. At-least-once delivery remains deduplicated.

P10-A1: outbox adds nullable `not_before` and terminal `cancelled`. AI-origin proposal mail gets org's
send delay at enqueue (default 120 s, including human/chat/policy decisions); staff mail stays immediate.
The job is due at not_before. Targeted claim waits for row locks and checks both delay and retry time
inside that transaction; a failed/rolled-back Stop cannot consume a delivery job as a skip.
`POST /outbox/{id}/stop` permits the linked action's approver or owner/admin, explicitly scopes the
RLS-exempt row and atomically locks pending→cancelled; sending/terminal/unknown gives too_late_to_stop.
Stop is audited and emits DeliveryStatusChanged; it never reverses the local business action.
`outbound.py` owns stop, origin delay and projections. Registry delivery reader aggregates states
none/scheduled/sending/delivered/failed/unknown/stopped; issues take priority over queued/terminal rows.
Public `outbound_queue_items(db,ctx,*,statuses,limit,cursor)` serves proposal-read work/exception views,
without recipients/variables/capability URLs. Unknown resolution rules remain unchanged.
`purchase_order` is text plus escaped HTML; the business's public email remains Reply-To.

SMTP retry tests freeze the application clock, verify no claim/SMTP send before next_attempt_at,
then advance to the exact due time before retrying. Header/stable-ID and TLS certificate checks stay intact.

C1 AI web-chat replies use conversations' withheld publication job and Stop endpoint; no simulated web outbox row
is queued for them. Mail keeps this module's A1 path. Approvals combines the registered mail and web delivery readers.

P10-B2: care mail uses the same delay/Stop outbox. A registry callback rechecks consent, preference/address, complaints and quiet hours before claim; blocked mail is cancelled with metadata evidence. No reverse AI import.

P10-C2b: transactional magic-link/change templates, `notify_permission` public staff fan-out and owner/admin + customer-read
`GET /notifications/simulated-emails` (scoped, simulated-only, last 24 h, read-only + audited view). Private mail
bodies include demo links; public APIs never do. Capability `link` variables are excluded from export. Erased,
restricted and real email-channel messages are not shown in this list. No SMTP adapter or service was added.
