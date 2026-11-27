# M21 · payments (v1, in progress)

| Field | Value |
|---|---|
| Release / phase | v1 · phases 1 and 2 built (connected accounts, pay links); phases 3 to 5 planned ([ADR-022](../03-decisions.md#adr-022-online-invoice-payments-stripe-connect-direct-charges-stripe-checkout), accepted 2026-11-27) |
| Tier / kind | A (outbound money movement, webhooks, public pay page) |
| Depends on | platform, org, sales, approvals, notifications (in `dependencies.py` and [02](../02-architecture.md)) |
| Owns tables | built: `payment_connections`, `payment_events`, `payment_links`, `payment_requests`; planned: `payment_disputes`, `payment_payouts` |

## Purpose
Let a business's customers pay issued invoices online through Stripe, and record that money in the sales ledger
(`invoice_payments`, `invoice_refunds`) so balances, aging, reminders and the QuickBooks sync keep working. The
module never sees card details: customers pay on a Stripe-hosted Checkout page. It does not pay suppliers, take
in-person card payments or keep a general ledger.

## How money moves
Boston AI Forward runs a Stripe Connect platform. Each business has its own connected account (full Stripe
Dashboard, Stripe owns losses, the account pays Stripe's fees). Payments are direct charges on that account.
Tandem talks to Stripe only through the **payments relay**, a small service run by Boston AI Forward that holds the
platform key, pins each deployment to its own connected accounts, and forwards Stripe's Connect webhooks to the
right deployment signed with that deployment's key.

## Tables (planned; columns settled in phase 1 and 2 cards)
- `payment_connections` — one per business: relay registration (sealed credential), Stripe account id, mode
  (test/live), charges and payouts enabled, requirements due, methods offered (card, US bank), pay-link default.
- `payment_requests` — one per pay attempt: invoice, signed pay-page token, Checkout Session id, amount and
  currency at creation, status (`open|completed|expired|pending_bank|failed`), expiry.
- `payment_events` — every forwarded Stripe event once (unique Stripe event id): type, account, received and
  processed times, outcome. Never holds card data.
- `payment_disputes` — Stripe dispute id, payment, amount, reason, due by, status, outcome.
- `payment_payouts` — Stripe payout id, amount, fees covered, arrival date, status; feeds QuickBooks.
- Sales gains: `invoice_payments.provider` and `external_id` (unique per business and provider), Stripe fee per
  payment, a disputed/reversed state, and an unapplied online payment record for money beyond the open balance.

## Business rules (planned)
1. Only an owner or admin sets up online payments or sends a card refund. Operators can share pay links.
2. Refused under `DATA_PROFILE=regulated`.
3. A Checkout Session charges the invoice's open balance at the moment the customer presses Pay, in the invoice's
   currency. Recording a payment by hand expires the invoice's open sessions.
4. A payment row is written only when Stripe confirms the money: at completion for cards, at the async success
   event for bank payments. Until then the invoice shows "payment pending".
5. Each Stripe event is applied at most once; the worker re-reads the object from Stripe before applying it, so
   order does not matter.
6. Online money beyond the open balance is recorded up to the balance; the rest is unapplied and the agent drafts a
   refund proposal for approval.
7. A card refund starts from a credit note in Tandem, goes through approvals, and uses the Tandem refund id as the
   Stripe idempotency key. A refund made in the Stripe Dashboard is matched by a drafted proposal, never silently.
8. A lost dispute reverses the payment and reopens the balance; a won one clears the flag.
9. Pay links are signed, rate limited, need no login and show only what the invoice shows. Onboarding links are
   made fresh on each click and never emailed.

## Phase 1 as built (2026-11-27)
- **Relay** (`backend/app/relay/`, runbook [payments-relay](../../runbooks/payments-relay.md)): `POST /v1/clients`
  registers one client per business (optional enrollment token, rate limited per address) and returns its shared
  secret once; the relay keeps it sealed. Signed calls (`Tandem-Relay-Signature`, 5-minute window, single-use nonce)
  create the business's connected account (Accounts v1, controller settings of ADR-022, idempotency key per client),
  read it, and make a fresh Account Link. The relay finds the account from the client, so no call names an account.
  `POST /v1/stripe/webhook` checks Stripe's signature, stores each event once and forwards an envelope (event id,
  type, account, object id, never the object) to the client's events URL, signed with the client's secret; failed
  deliveries back off from 1 minute to 1 day and stop after 3 days. Events URLs must be https on public addresses,
  except hosts the operator lists as private.
- **Tandem** (`modules/payments`): `GET /payments/stripe`, `POST /payments/stripe/onboarding` (country, default US;
  registers, creates the account if needed, returns a single-use Stripe link), `POST /payments/stripe/refresh`,
  `POST /payments/stripe/disconnect` (version-checked; the Stripe account is untouched). Status is `onboarding`
  until Stripe has the details, `restricted` when details are in but charges are off, `active` when charges are
  enabled, `disconnected` after Turn off. `POST /payments/relay/events` (public, signature-checked, 16 KB cap,
  600 per minute per address) stores the event once and queues `payments.apply_event`, which re-reads the account
  through the relay. A disconnected business stays disconnected whatever Stripe sends.
- **Permissions:** `payments.connection.read` (owner, admin, demo reviewer), `payments.connection.manage` (owner,
  admin). Policy 2026-11-27.1.
- **Defaults chosen for the open questions:** no platform fee anywhere (nothing is charged yet; phase 2 adds the
  setting at 0); cards only, bank payments wait for phase 2 behind a setting; only new Stripe accounts (connecting
  an existing one by OAuth is later); test mode until the relay holds a live key.

## Phase 2 as built (2026-11-27)
- **Pay link:** one per invoice (`payment_links`, only a hash of its token is stored). `POST
  /payments/invoices/{id}/link` returns it (made on first use), `.../link/reset` replaces it and the old one stops
  working; `GET /payments/invoices/{id}` gives availability, the link and every payment attempt. The token is signed
  with a key derived from `APP_SECRET_KEY` and names the business, invoice and link. Permission `payments.link.share`
  (owner, admin, demo reviewer, operator), policy 2026-11-27.2. The invoice page shows the card: copy link, replace
  link, last opened, attempts, and a note when money came in after the invoice was settled.
- **Pay page** (`/pay/<token>`, public widget bundle): business name, invoice number, due date, total and amount to
  pay; states payable, processing (bank debit on its way), paid, unavailable. `GET /payments/pay/{token}` (120 per
  minute per address, 60 per link) and `POST /payments/pay/{token}/checkout` (30 per hour per address, 10 per 10
  minutes per link). Both answer `Cache-Control: no-store`; Caddy and the app logs mask the token.
- **Checkout:** the relay creates a Checkout Session on the business's account for the open balance at that moment
  (mode payment, one line "Invoice <number>", metadata with Tandem's ids, no customer name or email), expiring after
  1 hour; an open session for the same amount with more than 10 minutes left is reused. Each attempt is a
  `payment_requests` row (`creating|open|processing|paid|expired|failed`).
- **Into the ledger:** `checkout.session.*` events reach Tandem as in phase 1; the worker re-reads the session
  through the relay and records the payment with `sales.record_online_payment`: `invoice_payments.provider` and
  `external_id` (the payment intent) are unique per business, so a payment is never recorded twice. Card payments
  are recorded at completion; bank debits stay `processing` until the async success event. Money beyond the open
  balance at that moment (for example cash recorded while the customer paid online) is kept on the attempt as
  surplus for the business to pay back from the Stripe Dashboard; phases 3 and 5 automate this.
- **Keeping it right:** any change to the invoice expires its open sessions (subscriber on `sales.document_changed`;
  a session found paid while expiring is applied instead). An hourly check per business re-reads open and processing
  attempts older than 15 minutes, so a lost webhook is caught, and fails attempts stuck in `creating`.
- **Defaults chosen:** cards only, bank debit (ACH, USD only) behind `PAYMENTS_BANK_TRANSFERS` (off, Q16); the
  relay's application fee `RELAY_APPLICATION_FEE_BPS` defaults to 0 (nothing charged, Q14); pay links do not expire
  (replace one to cut it off); the hourly check replaces the planned daily one.

## Phases
| Phase | What ships | Size |
|---|---|---|
| 0 | ADR-022, roadmap and this card | S |
| 1 | Relay service, Settings > Payments, connected accounts and status (**built 2026-11-27**) | L |
| 2 | Pay links and pay page, Checkout, webhooks into the ledger, unapplied payments, hourly check (**built 2026-11-27**) | L |
| 3 | Card refunds through approvals, Dashboard refund matching, disputes | M |
| 4 | Stripe fees, payouts, QuickBooks clearing and fees accounts, transfers | M |
| 5 | Agent work: pay links in drafted reminders, surplus refunds, unmatched refunds, payouts in the briefing | M |

Phases 1 and 2 are the first usable release. Later: deposits at booking, saved cards and autopay, paying part of an
invoice online, in-person payments, surcharges, Xero.

## Tests (must exist when built)
- Duplicate and out-of-order events change nothing twice.
- A cash payment racing an online payment never overpays an invoice.
- Bank payment pending then failed leaves the balance unchanged.
- Card refund failure reopens the refund; lost dispute after a partial refund keeps the ledger checks true.
- Relay refuses a deployment acting on another deployment's account, and replayed or unsigned events.
- A fake Stripe over `respx` (like the fake QuickBooks); one manual Stripe test-mode run before the first business.

## Open questions
Q13 to Q17 in [13](../13-risks-and-open-questions.md#open-questions): hosting, platform fee, Stripe platform account,
ACH, existing Stripe accounts.

## Out of scope
Card data in Tandem, in-person terminals, paying suppliers, a general ledger, Stripe Invoicing.
