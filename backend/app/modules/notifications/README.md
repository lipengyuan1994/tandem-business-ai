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
