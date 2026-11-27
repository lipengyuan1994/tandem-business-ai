# M21 · payments (v1, in progress)

| Field | Value |
|---|---|
| Release / phase | v1 · phases 1 to 4 built (connected accounts, pay links, card refunds and disputes, fees and payouts); phase 5 planned ([ADR-022](../03-decisions.md#adr-022-online-invoice-payments-stripe-connect-direct-charges-stripe-checkout), accepted 2026-11-27) |
| Tier / kind | A (outbound money movement, webhooks, public pay page) |
| Depends on | platform, org, sales, approvals, notifications (in `dependencies.py` and [02](../02-architecture.md)); accounting reads payouts from `payments.public` |
| Owns tables | built: `payment_connections`, `payment_events`, `payment_links`, `payment_requests`, `payment_refunds`, `payment_disputes`, `payment_payouts` |

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

## Phase 3 as built (2026-11-27)
- **What can be paid back to a card:** money a credit note owes back to the customer when staff recorded that refund
  with method "card", and the surplus left on an online payment (phase 2). `GET /payments/invoices/{id}` now lists
  `refund_options` (empty without `payments.refund.propose`, and under `DATA_PROFILE=regulated`), `refunds` and
  `disputes`. The amount always comes from Tandem's own records, never from the request, and never more than is left
  on the card payment after earlier card and Dashboard refunds. Cash or bank refunds on a credit note are never
  offered for the card.
- **Through approvals:** `POST /payments/invoices/{id}/card-refunds` (`payments.refund.propose`: owner, admin, demo reviewer,
  operator) writes a `payment_refunds` row (`proposed`) and a proposal `payments.card_refund`. Approving it needs
  `payments.refund.send` (owner, admin); self-approval follows the business's approval settings. Rejected, expired or
  cancelled proposals cancel the row so the amount can be asked for again. Only one live refund per credit-note
  refund. Policy 2026-11-27.3.
- **Sending:** approval queues `payments.send_refund`; the relay's `POST /v1/refunds` refunds the payment intent on
  the business's account with the Tandem refund id in the idempotency key and metadata, so a retry never pays twice.
  When the relay's application fee is above 0 the refund also returns the platform's share. A relay outage leaves
  the row `sending` and the job retries; the hourly check also re-sends rows stuck in `sending` for 15 minutes and
  re-reads `pending` ones. `refund.*` events re-read the refund through the relay (`GET /v1/refunds/{id}`) and set
  `pending|succeeded|failed`; a failure notifies owners and admins and the amount is offered again. The ledger is
  not touched: the credit note already recorded the refund, and a surplus was never on the invoice.
- **Refunds made in the Stripe Dashboard:** recorded as source `dashboard` and reported to owners and admins; the
  invoice is left alone (the business issues a credit note if the customer is owed it). Phase 5 has the agent draft
  that credit note. Refunds on payments Tandem does not know are ignored.
- **Disputes:** `charge.dispute.*` events re-read the dispute (`GET /v1/disputes/{id}`) and keep a
  `payment_disputes` row with Stripe's status, reason and answer-by date; opening one notifies holders of
  `payments.connection.manage` and the invoice shows it with its deadline. Disputes are answered in the Stripe
  Dashboard. While a dispute is open or lost, no card refund is offered for that payment. A lost dispute takes the
  disputed amount (never more than is still net paid) off the invoice once through `sales.reverse_online_payment`:
  the payment is voided, any part the bank did not take is re-recorded, and the invoice reopens. Won, prevented and
  `warning_closed` only clear the flag. A reversal that fails is kept with its error and retried hourly.
- **Defaults chosen:** refunds always go through approvals (no auto-approve); a Dashboard refund never changes the
  invoice on its own; the platform fee is refunded with the payment when one is charged.

## Phase 4 as built (2026-11-27)
- **Stripe's fee on each payment:** once a payment request is paid, the job `payments.read_fees` reads the payment's
  balance transaction through the relay (`GET /v1/payments/{payment_intent}/fee`) and stores `stripe_fee_minor` and
  `net_minor` on the request. The invoice's online payment card shows the fee and what reached the Stripe balance. A
  fee Stripe has not settled yet (bank debits) is filled in by the hourly `payments.check_payouts`.
- **Payouts:** `payout.*` events re-read the payout (`GET /v1/payouts/{id}`) into `payment_payouts` with Stripe's
  status and arrival date. Once a payout is paid, Tandem reads Stripe's own list of what it contained
  (`GET /v1/payouts/{id}/summary`: charges, refunds, disputes, other, fees, count; the relay pages through up to
  5,000 transactions and says when it could not read them all). A manual payout (started in the Stripe Dashboard)
  has no such list, so it is shown without totals. A failed payout notifies holders of `payments.connection.manage`.
  The hourly check re-reads payouts still on their way. Tandem never starts, changes or cancels a payout.
  `GET /payments/payouts` (`payments.connection.read`) lists the latest 100 for Settings > Payments.
- **QuickBooks:** accounting now depends on payments and reads `ledger_payouts` from `payments.public`. Two optional
  accounts join the mapping: a Stripe clearing account and a Stripe fees account. With a clearing account chosen,
  customer payments taken through Stripe are deposited there instead of the usual deposit account, and each paid
  payout is sent as a Transfer from clearing to the bank account on its arrival date, plus a Purchase for Stripe's
  fees from clearing to the fees account (sync records `payout_transfer` and `payout_fees`). A payout that fails
  after it was sent has both deleted in QuickBooks. Fees of a manual payout, or one whose list was too long, are
  left to the bookkeeper and say so in the sync log. Payouts in another currency than the QuickBooks company's are
  skipped. Card refunds and lost disputes keep their phase 3 handling (refunds entered by hand against the credit
  memo, lost disputes void the payment), so the bookkeeper should enter a Stripe card refund from the clearing account.
- **Defaults chosen:** no clearing account means no change to how payments are sent and no payouts are sent; fees
  are booked per payout (as Stripe settles them), not per payment.

## Phases
| Phase | What ships | Size |
|---|---|---|
| 0 | ADR-022, roadmap and this card | S |
| 1 | Relay service, Settings > Payments, connected accounts and status (**built 2026-11-27**) | L |
| 2 | Pay links and pay page, Checkout, webhooks into the ledger, unapplied payments, hourly check (**built 2026-11-27**) | L |
| 3 | Card refunds through approvals, Dashboard refund matching, disputes (**built 2026-11-27**) | M |
| 4 | Stripe fees, payouts, QuickBooks clearing and fees accounts, transfers (**built 2026-11-27**) | M |
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
