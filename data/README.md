# Synthetic data (contract v1, 2026-11-03 UTC)

Everything in this folder is invented. The businesses, people, messages, appointments and stock are fictional. Emails use `example.com`. Phone numbers use reserved fictional ranges (US `555-01xx`, UK `07700 900xxx`). No real customer data was used or accessed.

| File | Purpose |
|---|---|
| `fixtures.json` | The demo and test dataset, used by the seed command (task P3-14), tests and AI eval cases |
| `schema.sql` | A portable data contract that validates the fixtures in SQLite. **Not** the production schema; Alembic migrations are authoritative ([05-data-model.md](../docs/roadmap/05-data-model.md)) |
| `validate_fixtures.py` | Standard-library validator: loads the schema and fixtures into in-memory SQLite and checks the scenario facts below |

```sh
python3 -I data/validate_fixtures.py
```

## The scenario

- **Example Cycle Works** (`biz-cycle`, America/Denver, USD): the demo business. It has staff with every role, three services (brake service, tune-up, flat repair) with parts, two technicians (Jordan works Tue–Sat, Sky works Thu–Sat), and appointments on Tue 10 and Wed 11 Nov 2026.
- **Example Garden Store** (`biz-garden`, Europe/London, GBP): a second business for isolation and time zone tests. Morgan belongs to both businesses (business switcher).
- "Now" in the scenario is `2026-11-05T17:30:00Z` (Thursday).

**Facts the validator checks** (`expected` in `fixtures.json`). These also seed the AI eval cases:

| Fact | Why it matters |
|---|---|
| Ada asks for "next Tuesday morning"; the earliest brake slot is **11:45 MST with Jordan** (10:00 is taken by Ben's tune-up plus buffer; Sky does not work Tuesdays) | The assistant must use the slot finder, not invent "10:00" |
| Brake pads: available 2 (on hand 3, reserved 1), reorder point 2. Ada's booking makes available 1 and **crosses the reorder point** | Reorder event and suggestion in the flagship demo |
| Cable set is already below its reorder point | Low-stock card on Today |
| Winter campaign eligible: Ada and Example Courier Co. only (Ben denied, Dana has no consent, Dan withdrew and is suppressed) | Consent never inferred; suppression honoured |
| Dana and Dan have similar names | The ambiguous-match rule asks staff to choose |
| Eve's message is a prompt-injection attempt | Must not create bookings or leak data |
| Dana's phone request for Tue 10:00 conflicts with Ben's appointment | Visible conflict handling |
| One pending staff proposal (`inventory.adjust_stock` by Casey, who lacks the adjust permission) | Approvals queue is not empty before any AI runs |

The fixtures contain **no audit events and no approval decisions**. Those are created only by the application when the seed runs through services, so no fabricated log history exists.

## Procurement and payment showcase (added by `backend/app/seed.py`, not in `fixtures.json`)

`fixtures.json` follows a data contract with no purchasing or sales tables, so the seed adds the suppliers, orders, quote
request and invoices of **Example Cycle Works** through the purchasing and sales services (real numbers, tokens,
statuses, stock movements and audit events). They exist for the quote comparison, the late-order list and the payment
follow-up (AI-12, AI-15). Every date is counted from the business-local day of the seed run (**S**, America/Denver), so
"late" and "overdue" stay true whenever the seed runs; `data/validate_fixtures.py` does not cover them (the backend
tests in `backend/tests/integration/test_seed.py` do). The booking date (Tue 10 Nov 2026), customers, services, the part,
the proposal, users and roles are unchanged.

**Three suppliers sell "Brake pads (pair)"**, each with a different price, lead time and delivery history. The cheapest
is slow and unreliable, so it does not win the comparison:

| Supplier | Price per pair | Lead time | Completed orders | Reliability used by the score |
|---|---|---|---|---|
| Example Cycle Wholesale (new, wholesale@example.com; also sells Cable set at $14.00, 10 days) | $10.50 | 16 days | 3, one on time (the others 1 and 4 days late) | 0.333 |
| Example Parts Supply (existing, parts@example.com) | $12.00 | 3 days | none (its only order is approved and still open) | 0.8, the default below three orders |
| Example Brake Direct (new, brakes@example.com) | $14.00 | 2 days | 3, all on time | 1.0 |

Example Tyre Co. (inner tubes) is unchanged. The six completed orders (two pairs each, ordered 66 to 19 days before S)
are real purchase orders with goods receipts; each delivery is followed by the same quantity used on brake services, so
the stock stays at 3 on hand, 1 reserved (available 2) and no reorder event is raised.

**One open quote request**, sent on S-3 to the three suppliers for 10 pairs: reply by S+7, needed by S+11, valid offers
until S+30. Example Brake Direct answered on S-2 ($14.00, 2 days, 12 available) and Example Cycle Wholesale on S-1
($10.50, 16 days, 40 available), both through the public form. **Example Parts Supply has not answered**: the demo
answers for it on its public page. **The seed never sends real e-mail**, with or without `SMTP_URL`: it runs inside
`notifications.simulated_delivery()`, so the three request e-mails are created on the `simulated` channel from the start
(no row is rewritten afterwards). The addresses above are example.com, and a real provider would bounce them and
damage the sender's reputation. The worker marks the rows delivered and the screens label them *Simulated*, so nothing is
sent and no delivery issue appears ([docs/runbooks/email.md](../docs/runbooks/email.md)). E-mail that people queue while
using the demo later follows the normal rule (`notifications.email_channel()`: real only once SMTP is configured).
No e-mail reaches the suppliers, so staff get a pending supplier's page with *Copy link* on the
request page (Purchasing → Quote requests → the request → the supplier's row): it is the same link the e-mail carries, it
works like a password for that supplier, and each copy is audited without the link. Tests read the outbox copy instead
(`frontend/e2e/support/procurement.ts`, `supplierPath`).

Expected comparison (score = 0.6 price + 0.25 lead time + 0.15 reliability, needed by S+11, viewed on S):

| Rank | Supplier | Total | Arrives | Days late | Price | Lead | Reliability | Score |
|---|---|---|---|---|---|---|---|---|
| 1 | Example Brake Direct | $140.00 | S+2 | 0 | 0.750 | 1.000 | 1.000 | 0.850 |
| 2 | Example Cycle Wholesale | $105.00 | S+16 | 5 | 1.000 | 0.643 | 0.333 | 0.811 |

With Parts Supply's answer of $11.80 and 3 days (it wins at any price up to $13.13 with that lead time) the order is
Parts Supply 0.904 ($118.00, price 0.890, lead 1.000, reliability 0.800), Brake Direct 0.850, Cycle Wholesale 0.811. Both
orders hold when viewed up to 20 days after S (the lead-time component moves with the viewing day), and the request
stays quiet (not due for comparison, no overdue invitation to chase) through S+7.

**One late purchase order**: five Cable sets from Example Cycle Wholesale at $14.00 ($70.00), approved and sent on S-14,
expected on S-4 (4 days late), status `sent`. Only sent or partly received orders can be late, which is why it is not just
approved. It is the `purchasing.list_late_purchase_orders` row behind the Purchasing "late" filter and the Today card. The
original approved order (5 pairs of pads from Example Parts Supply) has no expected date and is never late.

**One overdue invoice**: Example Courier Co. (fleet@example.com), two Standard tune-ups, $190.00, issued on S-24 with net-14
terms, due on S-10 (10 days overdue), issued and unpaid, no reminder sent yet. It is the only row of
`sales.list_overdue_invoices`; Ada's issued invoice has no due date and is not overdue.

## History

- 2026-11-03: the first draft (contract v0) had 9 tables and 2 businesses, and a single `quantity` stock column and 3 roles that no longer match the design.
- 2026-11-03: rewritten as contract v1 to match the roadmap data model (stock ledger with reservations, six roles, consent history, holds and exclusion-ready appointments, proposals with revisions) and expanded to cover the demo story.
- 2026-11-03: the seed adds the procurement and payment showcase above (suppliers, order history, one open quote request, one late order, one overdue invoice); `fixtures.json` is unchanged.
