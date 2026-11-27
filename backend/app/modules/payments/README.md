# payments (M21)

Online invoice payments through Stripe Connect ([ADR-022](../../../../docs/roadmap/03-decisions.md)). Spec:
[M21](../../../../docs/roadmap/modules/M21-payments.md). Phase 1 of 5: the business's connected Stripe account.

Tandem never holds a Stripe key. Every Stripe call goes through the **payments relay** (`app/relay`, runbook
`docs/runbooks/payments-relay.md`); this module talks to it over HTTP only (`relay.py`, signed with `signing.py`,
the same scheme as the relay's own copy, checked by a test).

Tables (forced business RLS): `payment_connections` (one per business: relay URL, relay client id, the client
secret sealed with a key derived from `APP_SECRET_KEY`, Stripe account id, mode, charges/payouts enabled,
requirements due, status `onboarding|active|restricted|disconnected`) and `payment_events` (each forwarded Stripe
event once per business: id, type, account, object, outcome `pending|applied|ignored|failed`). Exports leave out
the sealed secret. No customer data yet, so no erasure handler.

Permissions: `payments.connection.read` (owner, admin, demo reviewer) and `payments.connection.manage` (owner,
admin).

Flow:
- `POST /payments/stripe/onboarding` registers the business with the relay the first time (outside any
  transaction; a second click reuses the stored client), creates the connected account if there is none, and
  returns a fresh single-use Stripe onboarding link. Stripe sends the owner back to
  `/settings/payments?stripe=return`, where the page re-reads the account.
- `POST /payments/relay/events` (public, signed by the relay with the business's client secret) stores the event
  once and queues `payments.apply_event`, which re-reads the account through the relay and updates the status.
- `POST /payments/stripe/disconnect` turns online payments off in Tandem only; Stripe keeps the account and setting
  up again reuses it.
- Refused under `DATA_PROFILE=regulated`; not offered while `PAYMENTS_RELAY_URL` is empty.

Tests: `tests/` run the real relay app in-process against a fake Stripe over `respx` (`app.relay.testing`).
