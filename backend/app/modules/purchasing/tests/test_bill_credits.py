"""Itemised supplier bills and supplier credits (with refunds when the bill was already paid)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.modules.identity.testing import login_as
from app.platform.db import transaction
from app.platform.registry import ErasureTarget, Registry
from app.platform.testing.auth import ctx_for

from .test_bills import TODAY, bill, new_bill, pay, received_po
from .test_purchasing import BASE, action, create, receive

# Synthetic data only.


def line(description: str = "Brake pads", quantity: str = "1", unit_cost: int = 1000, **over: Any) -> dict[str, Any]:
    return {"description": description, "quantity": quantity, "unit_cost_minor": unit_cost, **over}


def credit(shop: Any, row: dict[str, Any], amount: int, client: Any = None, **over: Any) -> Any:
    body = {"version": row["version"], "amount_minor": amount, "credit_date": TODAY, **over}
    return (client or shop["client"]).post(f"{BASE}/bills/{row['id']}/credits", json=body)


def void_credit(shop: Any, row: dict[str, Any], credit_id: str, client: Any = None) -> Any:
    return (client or shop["client"]).post(
        f"{BASE}/bills/{row['id']}/credits/{credit_id}/void", json={"version": row["version"]}
    )


REFUND = {"received_on": TODAY, "method": "bank_transfer", "reference": " RF-9 "}


def test_itemised_bill_totals_its_lines_rounding_half_up(shop: Any) -> None:
    row = bill(shop, total_minor=None, lines=[line("Brake pads", "2.5", 333), line(" Freight ", "1", 1500)])
    assert row["total_minor"] == 833 + 1500 and row["balance_minor"] == 2333
    assert [(x["position"], x["description"], x["amount_minor"]) for x in row["lines"]] == [
        (0, "Brake pads", 833),
        (1, "Freight", 1500),
    ]
    assert row["credits"] == []
    # A stated total must match the lines.
    assert bill(shop, total_minor=1000, lines=[line(unit_cost=1000)])["total_minor"] == 1000
    r = new_bill(shop, total_minor=999, lines=[line(unit_cost=1000)])
    assert r.status_code == 422 and r.json()["code"] == "purchasing.bill_total_mismatch"
    r = new_bill(shop, total_minor=None, lines=[line(unit_cost=0)])
    assert r.status_code == 422 and r.json()["code"] == "purchasing.bill_lines_total"
    for bad in (
        {"total_minor": None},  # neither total nor lines
        {"lines": []},
        {"lines": [line(description="  ")]},
        {"lines": [line(quantity="0")]},
        {"lines": [line(quantity="1.0005")]},
        {"lines": [line(unit_cost=-1)]},
    ):
        assert new_bill(shop, **bad).status_code == 422, bad


def test_bill_lines_link_to_catalog_items_and_their_own_order(shop: Any) -> None:
    po = received_po(shop)
    po_line = po["lines"][0]["id"]
    linked = line("Brake pads", "10", 1200, catalog_item_id=shop["item"], purchase_order_line_id=po_line)
    row = bill(shop, total_minor=None, purchase_order_id=po["id"], lines=[linked])
    assert row["total_minor"] == 12000
    assert (row["lines"][0]["catalog_item_id"], row["lines"][0]["purchase_order_line_id"]) == (shop["item"], po_line)

    r = new_bill(shop, total_minor=None, lines=[line(purchase_order_line_id=po_line)])  # no order on this bill
    assert r.status_code == 422 and r.json()["code"] == "purchasing.bill_line_not_on_order"
    r = new_bill(shop, total_minor=None, lines=[line(catalog_item_id=str(uuid4()))])
    assert r.status_code == 422 and r.json()["code"] == "purchasing.bill_line_item_unknown"


def test_editing_an_itemised_bill(shop: Any) -> None:
    row = bill(shop, total_minor=None, lines=[line(unit_cost=1000), line("Freight", "1", 500)])
    patch = shop["client"].patch
    r = patch(f"{BASE}/bills/{row['id']}", json={"version": row["version"], "total_minor": 2000})
    assert r.status_code == 422 and r.json()["code"] == "purchasing.bill_total_from_lines"

    r = patch(f"{BASE}/bills/{row['id']}", json={"version": row["version"], "lines": [line("Rotor", "3", 700)]})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["total_minor"] == 2100 and [x["description"] for x in row["lines"]] == ["Rotor"]

    # lines: null turns it back into a total-only bill, whose total can then be changed.
    r = patch(f"{BASE}/bills/{row['id']}", json={"version": row["version"], "lines": None, "total_minor": 2500})
    assert r.status_code == 200, r.text
    row = r.json()
    assert (row["total_minor"], row["lines"]) == (2500, [])

    row = credit(shop, row, 100).json()
    r = patch(f"{BASE}/bills/{row['id']}", json={"version": row["version"], "notes": "later"})
    assert r.status_code == 409 and r.json()["code"] == "purchasing.bill_locked"


def test_credit_on_an_unpaid_bill_takes_off_the_balance_and_can_be_voided(shop: Any) -> None:
    po = received_po(shop)
    row = bill(shop, purchase_order_id=po["id"])
    r = credit(shop, row, 2000, supplier_reference=" CR-1 ", reason="Two damaged units")
    assert r.status_code == 201, r.text
    row = r.json()
    assert (row["status"], row["amount_credited_minor"], row["amount_refunded_minor"], row["balance_minor"]) == (
        "partially_paid",
        2000,
        0,
        10000,
    )
    [c] = row["credits"]
    assert (c["amount_minor"], c["supplier_reference"], c["refund_minor"], c["refund_method"]) == (
        2000,
        "CR-1",
        0,
        None,
    )
    billing = shop["client"].get(f"{BASE}/purchase-orders/{po['id']}/billing").json()
    assert (billing["billed_minor"], billing["unbilled_received_minor"]) == (10000, 2000)

    r = credit(shop, row, 1000, refund=REFUND)
    assert r.status_code == 422 and r.json()["code"] == "purchasing.refund_not_needed"
    r = credit(shop, row, 10001)
    assert r.status_code == 409 and r.json()["code"] == "purchasing.credit_exceeds_bill"
    assert r.json()["creditable_minor"] == 10000
    r = credit(shop, row, 100, credit_date="2026-10-29")
    assert r.status_code == 422 and r.json()["code"] == "purchasing.credit_date_in_future"
    for bad in ({"amount_minor": 0}, {"reason": "x" * 501}, {"extra": 1}):
        assert credit(shop, row, 100, **bad).status_code == 422, bad

    r = shop["client"].post(f"{BASE}/bills/{row['id']}/void", json={"version": row["version"]})
    assert r.status_code == 409 and r.json()["code"] == "purchasing.bill_has_credits"
    r = void_credit(shop, row, c["id"])
    assert r.status_code == 200, r.text
    row = r.json()
    assert (row["status"], row["amount_credited_minor"], row["balance_minor"]) == ("open", 0, 12000)
    assert row["credits"][0]["voided_at"] is not None
    r = void_credit(shop, row, c["id"])
    assert r.status_code == 409 and r.json()["code"] == "purchasing.supplier_credit_already_voided"
    assert void_credit(shop, row, str(uuid4())).status_code == 404

    # Crediting the rest settles the bill without any money changing hands.
    row = credit(shop, row, 12000).json()
    assert (row["status"], row["balance_minor"]) == ("paid", 0)
    r = credit(shop, row, 1)
    assert r.status_code == 409 and r.json()["code"] == "purchasing.credit_exceeds_bill"
    assert r.json()["creditable_minor"] == 0


def test_credit_on_a_paid_bill_records_the_suppliers_refund(shop: Any) -> None:
    row = pay(shop, bill(shop), 12000).json()
    r = credit(shop, row, 3000)
    assert r.status_code == 409 and r.json()["code"] == "purchasing.refund_required"
    assert r.json()["refund_minor"] == 3000
    r = credit(shop, row, 3000, refund={**REFUND, "received_on": "2026-10-29"})
    assert r.status_code == 422 and r.json()["code"] == "purchasing.payment_date_in_future"

    r = credit(shop, row, 3000, refund=REFUND)
    assert r.status_code == 201, r.text
    row = r.json()
    assert (row["status"], row["amount_paid_minor"], row["amount_credited_minor"], row["amount_refunded_minor"]) == (
        "paid",
        12000,
        3000,
        3000,
    )
    assert row["balance_minor"] == 0
    [c] = row["credits"]
    assert (c["refund_minor"], c["refund_method"], c["refund_reference"]) == (3000, "bank_transfer", "RF-9")

    # The refunded payment cannot be voided while the credit stands.
    payment = row["payments"][0]["id"]
    r = shop["client"].post(f"{BASE}/bills/{row['id']}/payments/{payment}/void", json={"version": row["version"]})
    assert r.status_code == 409 and r.json()["code"] == "purchasing.payment_refunded"
    row = void_credit(shop, row, c["id"]).json()
    assert (row["status"], row["amount_refunded_minor"], row["balance_minor"]) == ("paid", 0, 0)
    r = shop["client"].post(f"{BASE}/bills/{row['id']}/payments/{payment}/void", json={"version": row["version"]})
    assert r.status_code == 200, r.text


def test_credit_on_a_part_paid_bill_splits_between_balance_and_refund(shop: Any) -> None:
    row = pay(shop, bill(shop), 10000).json()
    row = credit(shop, row, 5000, refund=REFUND).json()
    assert (row["amount_credited_minor"], row["amount_refunded_minor"], row["balance_minor"]) == (5000, 3000, 0)
    assert row["status"] == "paid"


def test_credit_permissions_and_idempotency(shop: Any) -> None:
    row = bill(shop)
    auditor = login_as(shop["app"], shop["business"], ["auditor"])
    assert credit(shop, row, 100, client=auditor).status_code == 403
    manager = login_as(shop["app"], shop["business"], ["inventory_manager"])
    r = credit(shop, row, 100, client=manager)
    assert r.status_code == 201, r.text
    row = r.json()
    assert void_credit(shop, row, row["credits"][0]["id"], client=manager).status_code == 403

    key = {"Idempotency-Key": str(uuid4())}
    body = {"version": row["version"], "amount_minor": 200, "credit_date": TODAY}
    first = shop["client"].post(f"{BASE}/bills/{row['id']}/credits", json=body, headers=key)
    again = shop["client"].post(f"{BASE}/bills/{row['id']}/credits", json=body, headers=key)
    assert first.status_code == again.status_code == 201
    assert len(again.json()["credits"]) == 2 and again.json()["amount_credited_minor"] == 300


def test_payables_aging_counts_credits_by_their_date(shop: Any) -> None:
    row = bill(shop, bill_date="2026-09-01", due_date="2026-10-01", total_minor=5000)
    credit(shop, row, 2000, credit_date="2026-10-20")

    def owed(as_of: str) -> int:
        report = shop["client"].get(f"{BASE}/payables/aging", params={"as_of": as_of}).json()
        return int(report["totals"][0]["total_minor"])

    assert owed(TODAY) == 3000
    assert owed("2026-10-19") == 5000

    paid = bill(shop, bill_date="2026-10-01", total_minor=1000)
    paid = pay(shop, paid, 1000, paid_on="2026-10-02").json()
    credit(shop, paid, 400, credit_date="2026-10-25", refund={**REFUND, "received_on": "2026-10-27"})
    assert owed(TODAY) == 3000  # credited and refunded: nothing owed either way
    # Credited but the refund not yet received: the supplier owes money back, which is not a payable.
    assert owed("2026-10-26") == 3000
    assert owed("2026-10-24") == 3000  # paid in full, credit not yet given


def test_export_and_erasure_cover_lines_and_credits(shop: Any, registry: Registry) -> None:
    row = bill(shop, total_minor=None, lines=[line("Parts for Example Person", "1", 900)])
    credit(shop, row, 100, reason="Returned by Example Person")
    ctx = ctx_for(shop["business"])
    with transaction(ctx) as db:
        counts = registry.erasure_handlers["purchasing"](db, ctx, ErasureTarget(uuid4(), "Example Person", (), ()))
        exported = registry.export_handlers["purchasing"](db, ctx)
    assert counts["supplier_bill_lines_redacted"] == 1 and counts["supplier_credits_redacted"] == 1
    assert exported["supplier_bill_lines"][0]["description"] == "Parts for [erased]"
    assert exported["supplier_credits"][0]["reason"] == "Returned by [erased]"


def test_receipt_prefill_subtracts_already_billed_and_rechecks_stale_quantities(shop: Any) -> None:
    po = action(shop, action(shop, create(shop, "10"), "approve"), "send")
    r = receive(shop, po, "4")
    assert r.status_code == 201, r.text
    po_line = po["lines"][0]["id"]
    linked = line("Brake pads", "2", 1200, purchase_order_line_id=po_line)
    arguments = dict(total_minor=None, purchase_order_id=po["id"], lines=[linked], match_received=True)
    billing_url = f"{BASE}/purchase-orders/{po['id']}/billing"
    bill(shop, **arguments)
    snapshot = shop["client"].get(billing_url).json()
    assert snapshot["unbilled_received_minor"] == 2400
    [remaining] = snapshot["remaining_received_lines"]
    assert remaining["purchase_order_line_id"] == po_line and Decimal(remaining["quantity"]) == 2
    later = bill(shop, **arguments)  # another staff member bills the remaining receipts
    stale = new_bill(shop, **arguments)
    assert stale.status_code == 409 and stale.json()["code"] == "purchasing.bill_receipts_changed"
    assert shop["client"].get(billing_url).json()["remaining_received_lines"] == []

    voided = shop["client"].post(f"{BASE}/bills/{later['id']}/void", json={"version": later["version"]})
    assert voided.status_code == 200, voided.text
    [available] = shop["client"].get(billing_url).json()["remaining_received_lines"]
    assert Decimal(available["quantity"]) == 2
    # A deliberate advance bill remains allowed; a total-only bill cannot be split among order lines.
    bill(shop, purchase_order_id=po["id"], total_minor=9000)
    assert shop["client"].get(billing_url).json()["remaining_received_lines"] is None
    assert new_bill(shop, **arguments).status_code == 409


def test_refund_requires_enough_dated_payments_and_does_not_reopen_paid_bill(shop: Any) -> None:
    row = bill(shop, bill_date="2026-10-20", total_minor=12000)
    row = pay(shop, row, 1000, paid_on="2026-10-22").json()
    row = pay(shop, row, 11000, paid_on="2026-10-26").json()

    def payable(as_of: str) -> int:
        r = shop["client"].get(f"{BASE}/payables/aging", params={"as_of": as_of})
        assert r.status_code == 200, r.text
        return sum(part["total_minor"] for part in r.json()["totals"])

    r = credit(shop, row, 3000, refund={**REFUND, "received_on": "2026-10-23"})
    assert r.status_code == 409 and r.json()["code"] == "purchasing.refund_exceeds_dated_payments"
    assert payable("2026-10-23") == 11000
    r = credit(shop, row, 3000, refund={**REFUND, "received_on": "2026-10-26"})
    assert r.status_code == 201, r.text
    assert payable("2026-10-26") == payable(TODAY) == 0

    # Voiding is also date-sensitive: the later installment cannot back a previously recorded refund.
    other = bill(shop, bill_date="2026-10-20", total_minor=12000)
    other = pay(shop, other, 4000, paid_on="2026-10-22").json()
    other = pay(shop, other, 8000, paid_on="2026-10-26").json()
    other = credit(shop, other, 3000, refund={**REFUND, "received_on": "2026-10-23"}).json()
    first_payment = other["payments"][0]["id"]
    r = shop["client"].post(
        f"{BASE}/bills/{other['id']}/payments/{first_payment}/void", json={"version": other["version"]}
    )
    assert r.status_code == 409 and r.json()["code"] == "purchasing.payment_refunded"
