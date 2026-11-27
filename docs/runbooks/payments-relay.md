# Payments relay

The payments relay is the small service that holds Boston AI Forward's Stripe platform key so no Tandem server has to
([ADR-022](../roadmap/03-decisions.md#adr-022-online-invoice-payments-stripe-connect-direct-charges-stripe-checkout),
module card [M21](../roadmap/modules/M21-payments.md)). Each business's Tandem registers with it from
Settings > Payments; owners never paste keys. Code: `backend/app/relay/` (same image as the backend, its own
entry point `app.relay.main:create_app`).

**Status (2026-11-27): phases 1 and 2, test mode only.** Nothing here has been run against Stripe yet: Boston AI Forward's
Stripe platform account does not exist. Only the owner can open it, accept Stripe's Connect terms and create keys.

## What it stores

A SQLite file (`RELAY_DATABASE_URL`, default `sqlite:///relay.db`): one row per registered business (client id, the
client's shared secret sealed with `RELAY_SECRET_KEY`, its callback URLs, its Stripe account id), Stripe events
waiting for delivery, and recently used request nonces. No card data and no customer data: events are forwarded as
envelopes (event id, type, account, object id), and Tandem reads what it needs through the relay. Back the file up
with the host's other data; losing it means every business registers again and must be reconnected by hand to its
existing Stripe account.

## Settings

| Variable | Meaning |
|---|---|
| `RELAY_SECRET_KEY` | At least 32 characters. Seals client secrets. Changing it makes every business register again. |
| `RELAY_DATABASE_URL` | SQLAlchemy URL of the relay's store, e.g. `sqlite:////relay-data/relay.db`. |
| `RELAY_STRIPE_SECRET_KEY` | The platform's **restricted** key (Accounts and Account Links write, Checkout Sessions write, Events read; later Refunds). Empty: the relay runs and answers 503 to anything that needs Stripe. A `sk_live_`/`rk_live_` key puts every business in live mode. |
| `RELAY_STRIPE_WEBHOOK_SECRET` | Signing secret of the Connect webhook endpoint below. |
| `RELAY_STRIPE_API_VERSION` | Pin Stripe's API version once the platform account exists; empty uses the account default. |
| `RELAY_ENROLLMENT_TOKEN` | When set, a Tandem server must present it to register (give it to the people who run Tandem servers). Empty lets anyone register, which is only right when the relay is reachable from your own Tandem alone. |
| `RELAY_ENROLLMENTS_PER_HOUR` | Registrations per source address per hour (default 20). |
| `RELAY_PRIVATE_CALLBACK_HOSTS` | Host names that may receive events over plain http or on a private address, for a relay next to Tandem (e.g. `api`). Every other events URL must be https and resolve only to public addresses. |
| `RELAY_APPLICATION_FEE_BPS` | Boston AI Forward's fee on each online payment, in basis points of the amount (default 0, at most 1000). It is taken from the business's payment by Stripe. Changing it changes what businesses pay: owner decision (Q14). |
| `RELAY_DELIVER_INTERVAL_SECONDS` | Seconds between delivery sweeps (default 30). |

Give the relay only `RELAY_*` variables in production: it needs none of Tandem's secrets.

On each Tandem server: `PAYMENTS_BANK_TRANSFERS=true` to offer US bank debit for USD invoices (off by default),
`PAYMENTS_RELAY_URL` (the relay's URL), `PAYMENTS_RELAY_ENROLLMENT_TOKEN`, and the relay's host
in `EGRESS_ALLOWLIST`. For a relay on the same Docker network set `PAYMENTS_RELAY_PRIVATE=true` instead, and
`PAYMENTS_EVENTS_URL=http://api:8000/api/v1/payments/relay/events` so events stay on that network.

## Stripe setup (owner, once the platform account exists)

1. In the Stripe Dashboard (test mode first): Connect, platform profile, accept the Connect terms (owner only).
2. Create a restricted key with the permissions above and put it in `RELAY_STRIPE_SECRET_KEY`.
3. Add a webhook endpoint `https://<relay host>/v1/stripe/webhook`, "Events on connected accounts", with at least
   `account.updated`, `account.application.deauthorized`, `checkout.session.completed`,
   `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed` and `checkout.session.expired`; put its signing secret in `RELAY_STRIPE_WEBHOOK_SECRET`.
4. Restart the relay, open Settings > Payments in a test business, and walk through Stripe's test onboarding.
5. Live mode is a separate key and endpoint, and only after the owner decides to go live.

## Running it

Development: `docker compose -f deploy/compose/compose.dev.yml --profile payments up -d payments-relay` with the
`RELAY_*` and `PAYMENTS_*` lines from `.env.example`. To get Stripe test events locally use the Stripe CLI:
`stripe listen --forward-connect-to http://localhost:8100/v1/stripe/webhook` and use the secret it prints as
`RELAY_STRIPE_WEBHOOK_SECRET`.

Production hosting is not decided (Q13). Whichever host runs it: one process (`uvicorn app.relay.main:create_app
--factory --host 0.0.0.0 --port 8100`), behind TLS, with a persistent volume for the SQLite file.

## Checks

- `GET /healthz` answers `{"ok": true, "mode": "test"|"live", "stripe_configured": true|false}`.
- Events that cannot be delivered are retried for 3 days, then dropped and logged (`relay.event_given_up`).
  Tandem's hourly check re-reads every payment attempt still open or processing after 15 minutes, so a payment
  whose event was lost is still recorded (job `payments.check_requests`).
- A Tandem server that shows "The payments service no longer recognises this business" lost its registration (relay
  store lost or `RELAY_SECRET_KEY` changed): remove the business's row from `payment_connections` only after
  confirming with its owner, then set up again; Stripe keeps the existing account, which the relay must be told about
  by hand (not automated yet).
