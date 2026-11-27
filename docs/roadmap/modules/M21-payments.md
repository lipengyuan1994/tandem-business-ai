# M21 · payments (v1, planned)

| Field | Value |
|---|---|
| Release / phase | v1 · planned; nothing built yet ([ADR-022](../03-decisions.md#adr-022-online-invoice-payments-stripe-connect-direct-charges-stripe-checkout), proposed 2026-11-27) |
| Tier / kind | A (outbound money movement, webhooks, public pay page) |
| Depends on | platform, org, sales, approvals, notifications (added to `dependencies.py` and [02](../02-architecture.md) when built) |
| Owns tables (planned) | `payment_connections`, `payment_requests`, `payment_events`, `payment_disputes`, `payment_payouts` |

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

## Phases
| Phase | What ships | Size |
|---|---|---|
| 0 | ADR-022, roadmap and this card | S |
| 1 | Relay service, Settings > Payments, connected accounts and status | L |
| 2 | Pay links and pay page, Checkout, webhooks into the ledger, unapplied payments, daily check | L |
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
