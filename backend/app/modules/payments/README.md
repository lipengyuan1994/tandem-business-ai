# payments (M21)

Online invoice payments through Stripe Connect ([ADR-022](../../../../docs/roadmap/03-decisions.md)). Spec:
[M21](../../../../docs/roadmap/modules/M21-payments.md). Phases 1 to 3 of 5: the business's connected Stripe
account, pay links that let customers pay invoices on Stripe Checkout, and card refunds and disputes.

Tandem never holds a Stripe key. Every Stripe call goes through the **payments relay** (`app/relay`, runbook
`docs/runbooks/payments-relay.md`); this module talks to it over HTTP only (`relay.py`, signed with `signing.py`,
the same scheme as the relay's own copy, checked by a test).

Tables (forced business RLS): `payment_connections` (one per business: relay URL, relay client id, the client
secret sealed with a key derived from `APP_SECRET_KEY`, Stripe account id, mode, charges/payouts enabled,
requirements due, status `onboarding|active|restricted|disconnected`) and `payment_events` (each forwarded Stripe
event once per business: id, type, account, object, outcome `pending|applied|ignored|failed`). Exports leave out
the sealed secret. Phase 2 adds `payment_links` (one active link per invoice, only the token's hash stored) and
`payment_requests` (one per Checkout attempt: amount, session id and page address, status
`creating|open|processing|paid|expired|failed`, what was applied to the invoice and any surplus). Exports leave out
token hashes and Stripe page addresses. Phase 3 adds `payment_refunds` (card refunds: source
`credit_note|surplus|dashboard`, amount, status `proposed|sending|pending|succeeded|failed|canceled`, proposal, Stripe
refund id) and `payment_disputes` (Stripe dispute id, amount, status, reason, answer-by date, outcome
`open|won|lost`, amount taken off the invoice). Nothing names a customer, so there is still no erasure handler.

Permissions: `payments.connection.read` (owner, admin, demo reviewer), `payments.connection.manage` (owner,
admin), `payments.link.share` (owner, admin, demo reviewer, operator), `payments.refund.propose` (owner, admin,
demo reviewer, operator) and `payments.refund.send` (owner, admin).

Flow:
- `POST /payments/stripe/onboarding` registers the business with the relay the first time (outside any
  transaction; a second click reuses the stored client), creates the connected account if there is none, and
  returns a fresh single-use Stripe onboarding link. Stripe sends the owner back to
  `/settings/payments?stripe=return`, where the page re-reads the account.
- `POST /payments/relay/events` (public, signed by the relay with the business's client secret) stores the event
  once and queues `payments.apply_event`, which re-reads the account through the relay and updates the status.
- `POST /payments/stripe/disconnect` turns online payments off in Tandem only; Stripe keeps the account and setting
  up again reuses it.
- `POST /payments/invoices/{id}/link` (and `/link/reset`) gives the invoice's pay link (`tokens.py`, signed with a
  key derived from `APP_SECRET_KEY`). The public `api_pay.py` routes serve `/pay/<token>`: the page, and
  `/checkout`, which asks the relay for a Checkout Session for the open balance (`paylinks.start_checkout`).
- `checkout.session.*` events queue `payments.apply_event` like account events; `paylinks.apply` re-reads the
  session and records the money through `sales.record_online_payment` (idempotent on the payment intent).
  `sales.document_changed` expires open sessions of a changed invoice (`payments.close_requests`), and the hourly
  `payments.check_requests` re-reads attempts whose event may have been lost.
- `POST /payments/invoices/{id}/card-refunds` (`refunds.py`) proposes paying a credit-note card refund or online
  surplus back to the card; the amount comes from Tandem's records. Approving the `payments.card_refund` proposal
  queues `payments.send_refund`, which calls the relay with the refund id as idempotency key. `refund.*` events and
  the hourly `payments.check_refunds` finish it; a refund made in the Stripe Dashboard is recorded as `dashboard` and
  reported, never put on the invoice.
- `charge.dispute.*` events (`disputes.py`) track disputes and notify owners and admins; a lost one calls
  `sales.reverse_online_payment` once. No card refund is offered while a dispute is open or lost.
- Refused under `DATA_PROFILE=regulated`; not offered while `PAYMENTS_RELAY_URL` is empty.

Tests: `tests/` run the real relay app in-process against a fake Stripe over `respx` (`app.relay.testing`).
