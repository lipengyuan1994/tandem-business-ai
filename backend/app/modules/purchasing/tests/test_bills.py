"""Supplier bills, bill payments, voids, permissions and payables aging."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.modules.identity.testing import login_as
from app.platform.db import transaction
from app.platform.registry import ErasureTarget, Registry
from app.platform.testing.auth import ctx_for

from .test_purchasing import BASE, action, create, receive

# The shop fixture's clock is 2026-10-28 12:00 UTC, which is 2026-10-28 in Denver.
TODAY = "2026-10-28"


def received_po(shop: Any, quantity: str = "10") -> dict[str, Any]:
    po = action(shop, action(shop, create(shop, quantity), "approve"), "send")
    r = receive(shop, po, quantity)
    assert r.status_code == 201, r.text
    return dict(r.json())


def new_bill(shop: Any, client: Any = None, **over: Any) -> Any:
    body = {"supplier_id": shop["supplier"]["id"], "bill_date": TODAY, "total_minor": 12000, **over}
    return (client or shop["client"]).post(f"{BASE}/bills", json=body)


def bill(shop: Any, **over: Any) -> dict[str, Any]:
    r = new_bill(shop, **over)
    assert r.status_code == 201, r.text
    return dict(r.json())


def pay(shop: Any, row: dict[str, Any], amount: int, client: Any = None, **over: Any) -> Any:
    body = {"version": row["version"], "amount_minor": amount, "paid_on": TODAY, "method": "bank_transfer", **over}
    return (client or shop["client"]).post(f"{BASE}/bills/{row['id']}/payments", json=body)


def test_bill_against_received_po_then_partial_and_full_payment(shop: Any) -> None:
    po = received_po(shop)
    billing = shop["client"].get(f"{BASE}/purchase-orders/{po['id']}/billing").json()
    [remaining] = billing.pop("remaining_received_lines")
    assert remaining["purchase_order_line_id"] == po["lines"][0]["id"]
    assert Decimal(remaining["quantity"]) == 10
    assert billing == {
        "purchase_order_id": po["id"],
        "currency": "USD",
        "ordered_minor": 12000,
        "received_minor": 12000,
        "billed_minor": 0,
        "unbilled_received_minor": 12000,
    }

    row = bill(shop, purchase_order_id=po["id"], supplier_reference="  INV-77 ", due_date="2026-11-27", notes=" ")
    assert row["status"] == "open" and row["supplier_name"] == "Example Parts"
    assert (row["supplier_reference"], row["notes"], row["currency"]) == ("INV-77", None, "USD")
    assert (row["purchase_order_number"], row["balance_minor"], row["amount_paid_minor"]) == ("PO-2026-0001", 12000, 0)
    assert shop["client"].get(f"{BASE}/purchase-orders/{po['id']}/billing").json()["billed_minor"] == 12000

    r = pay(shop, row, 5000, reference="  ACH-1 ")
    assert r.status_code == 201, r.text
    row = r.json()
    assert (row["status"], row["amount_paid_minor"], row["balance_minor"]) == ("partially_paid", 5000, 7000)
    assert [(p["amount_minor"], p["method"], p["reference"]) for p in row["payments"]] == [
        (5000, "bank_transfer", "ACH-1")
    ]

    row = pay(shop, row, 7000, method="check", reference="").json()
    assert (row["status"], row["balance_minor"]) == ("paid", 0)
    assert [p["reference"] for p in row["payments"]] == ["ACH-1", None]
    assert pay(shop, row, 1).status_code == 409  # paid bills take no more payments

    listed = shop["client"].get(f"{BASE}/bills", params={"status": ["paid"]}).json()["items"]
    assert [b["id"] for b in listed] == [row["id"]] and listed[0]["payments"] == []
    assert shop["client"].get(f"{BASE}/bills", params={"status": ["open"]}).json()["items"] == []
    by_po = shop["client"].get(f"{BASE}/bills", params={"purchase_order_id": po["id"]}).json()["items"]
    assert len(by_po) == 1


def test_bill_input_checks(shop: Any) -> None:
    r = new_bill(shop, bill_date="2026-10-29")
    assert r.status_code == 422 and r.json()["code"] == "purchasing.bill_date_in_future"
    for bad in (
        {"total_minor": 0},
        {"total_minor": "10"},
        {"due_date": "2026-10-01"},
        {"supplier_reference": "x" * 101},
        {"extra": 1},
    ):
        assert new_bill(shop, **bad).status_code == 422, bad

    draft = create(shop)
    r = new_bill(shop, purchase_order_id=draft["id"])
    assert r.status_code == 409 and r.json()["code"] == "purchasing.bill_po_not_billable"

    other = shop["client"].post(f"{BASE}/suppliers", json={"name": "Other Example Supply"}).json()
    po = received_po(shop)
    r = new_bill(shop, supplier_id=other["id"], purchase_order_id=po["id"])
    assert r.status_code == 422 and r.json()["code"] == "purchasing.bill_po_supplier_mismatch"
    assert new_bill(shop, supplier_id=str(uuid4())).status_code == 404


def test_duplicate_supplier_reference_is_refused_until_the_first_bill_is_void(shop: Any) -> None:
    first = bill(shop, supplier_reference="INV-1")
    r = new_bill(shop, supplier_reference="inv-1")
    assert r.status_code == 409 and r.json()["code"] == "purchasing.duplicate_bill"
    assert r.json()["bill_id"] == first["id"]
    other = shop["client"].post(f"{BASE}/suppliers", json={"name": "Other Example Supply"}).json()
    assert new_bill(shop, supplier_id=other["id"], supplier_reference="INV-1").status_code == 201

    voided = shop["client"].post(f"{BASE}/bills/{first['id']}/void", json={"version": first["version"]})
    assert voided.status_code == 200 and voided.json()["status"] == "void" and voided.json()["balance_minor"] == 0
    assert new_bill(shop, supplier_reference="INV-1").status_code == 201


def test_edit_open_bill_and_lock_after_payment(shop: Any) -> None:
    row = bill(shop, supplier_reference="INV-2")
    r = shop["client"].patch(
        f"{BASE}/bills/{row['id']}",
        json={"version": row["version"], "total_minor": 15000, "due_date": "2026-11-10", "notes": "Freight included"},
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert (row["total_minor"], row["due_date"], row["notes"], row["version"]) == (
        15000,
        "2026-11-10",
        "Freight included",
        2,
    )
    stale = shop["client"].patch(f"{BASE}/bills/{row['id']}", json={"version": 1, "notes": "x"})
    assert stale.status_code == 409 and stale.json()["code"] == "purchasing.version_conflict"
    r = shop["client"].patch(f"{BASE}/bills/{row['id']}", json={"version": row["version"], "bill_date": "2026-11-20"})
    assert r.status_code == 422 and r.json()["code"] == "purchasing.bill_date_in_future"
    r = shop["client"].patch(f"{BASE}/bills/{row['id']}", json={"version": row["version"], "total_minor": None})
    assert r.status_code == 422

    row = pay(shop, row, 1000).json()
    r = shop["client"].patch(f"{BASE}/bills/{row['id']}", json={"version": row["version"], "notes": "later"})
    assert r.status_code == 409 and r.json()["code"] == "purchasing.bill_locked"


def test_overpayment_future_date_and_void_rules(shop: Any) -> None:
    row = bill(shop)
    r = pay(shop, row, 12001)
    assert r.status_code == 409 and r.json()["code"] == "purchasing.overpayment" and r.json()["balance_minor"] == 12000
    r = pay(shop, row, 100, paid_on="2026-10-29")
    assert r.status_code == 422 and r.json()["code"] == "purchasing.payment_date_in_future"
    for bad in ({"amount_minor": 0}, {"method": "crypto"}, {"reference": "x" * 201}):
        assert pay(shop, row, 100, **bad).status_code == 422, bad

    row = pay(shop, row, 12000).json()
    assert row["status"] == "paid"
    r = shop["client"].post(f"{BASE}/bills/{row['id']}/void", json={"version": row["version"]})
    assert r.status_code == 409 and r.json()["code"] == "purchasing.bill_has_payments"

    payment = row["payments"][0]["id"]
    r = shop["client"].post(f"{BASE}/bills/{row['id']}/payments/{payment}/void", json={"version": row["version"]})
    assert r.status_code == 200, r.text
    row = r.json()
    assert (row["status"], row["amount_paid_minor"], row["balance_minor"]) == ("open", 0, 12000)
    assert row["payments"][0]["voided_at"] is not None
    again = shop["client"].post(f"{BASE}/bills/{row['id']}/payments/{payment}/void", json={"version": row["version"]})
    assert again.status_code == 409 and again.json()["code"] == "purchasing.bill_payment_already_voided"
    missing = shop["client"].post(f"{BASE}/bills/{row['id']}/payments/{uuid4()}/void", json={"version": row["version"]})
    assert missing.status_code == 404

    row = (
        shop["client"].post(f"{BASE}/bills/{row['id']}/void", json={"version": row["version"], "reason": "Dup"}).json()
    )
    assert row["status"] == "void"
    assert pay(shop, row, 100).status_code == 409
    r = shop["client"].post(f"{BASE}/bills/{row['id']}/void", json={"version": row["version"]})
    assert r.status_code == 409


def test_payment_idempotency_key_replays_once(shop: Any) -> None:
    row = bill(shop)
    body = {"version": row["version"], "amount_minor": 2500, "paid_on": TODAY, "method": "cash"}
    headers = {"Idempotency-Key": "bill-pay-1"}
    first = shop["client"].post(f"{BASE}/bills/{row['id']}/payments", json=body, headers=headers)
    second = shop["client"].post(f"{BASE}/bills/{row['id']}/payments", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert shop["client"].get(f"{BASE}/bills/{row['id']}").json()["amount_paid_minor"] == 2500


def test_role_permissions(shop: Any) -> None:
    row = bill(shop)
    manager = login_as(shop["app"], shop["business"], ["inventory_manager"])
    assert new_bill(shop, manager, total_minor=500).status_code == 201
    assert manager.get(f"{BASE}/bills/{row['id']}").status_code == 200
    assert pay(shop, row, 100, client=manager).status_code == 403
    assert manager.post(f"{BASE}/bills/{row['id']}/void", json={"version": row["version"]}).status_code == 403

    auditor = login_as(shop["app"], shop["business"], ["auditor"])
    assert auditor.get(f"{BASE}/bills").status_code == 200
    assert auditor.get(f"{BASE}/payables/aging").status_code == 200
    assert new_bill(shop, auditor).status_code == 403

    operator = login_as(shop["app"], shop["business"], ["operator"])
    assert operator.get(f"{BASE}/bills").status_code == 403
    assert operator.get(f"{BASE}/payables/aging").status_code == 403

    admin = login_as(shop["app"], shop["business"], ["admin"])
    assert pay(shop, row, 100, client=admin).status_code == 201


def test_payables_aging_buckets_by_due_date_as_of_any_day(shop: Any, registry: Registry) -> None:
    other = shop["client"].post(f"{BASE}/suppliers", json={"name": "Another Example Supply"}).json()
    bill(shop, bill_date="2026-10-20", due_date="2026-11-05", total_minor=1000)  # not yet due
    late = bill(shop, bill_date="2026-09-01", due_date="2026-10-01", total_minor=2000)  # 27 days late
    bill(shop, bill_date="2026-06-01", total_minor=3000)  # no due date: 149 days from bill date
    bill(shop, supplier_id=other["id"], bill_date="2026-08-15", due_date="2026-08-25", total_minor=4000)  # 64 days
    gone = bill(shop, supplier_id=other["id"], bill_date="2026-08-15", total_minor=9999)
    shop["client"].post(f"{BASE}/bills/{gone['id']}/void", json={"version": gone["version"]})
    paid_late = pay(shop, late, 500, paid_on="2026-10-27").json()
    assert paid_late["balance_minor"] == 1500

    report = shop["client"].get(f"{BASE}/payables/aging").json()
    assert report["as_of"] == TODAY
    rows = {r["supplier_name"]: r for r in report["suppliers"]}
    parts = rows["Example Parts"]
    assert (parts["current_minor"], parts["days_1_30_minor"], parts["days_over_90_minor"]) == (1000, 1500, 3000)
    assert (parts["total_minor"], parts["bill_count"]) == (5500, 3)
    assert (rows["Another Example Supply"]["days_61_90_minor"], rows["Another Example Supply"]["bill_count"]) == (
        4000,
        1,
    )
    assert [r["supplier_name"] for r in report["suppliers"]] == ["Example Parts", "Another Example Supply"]
    assert report["totals"] == [
        {
            "currency": "USD",
            "current_minor": 1000,
            "days_1_30_minor": 1500,
            "days_31_60_minor": 0,
            "days_61_90_minor": 4000,
            "days_over_90_minor": 3000,
            "total_minor": 9500,
            "bill_count": 4,
        }
    ]

    # Earlier: the 27 Oct payment had not happened yet and the 20 Oct bill did not exist.
    earlier = shop["client"].get(f"{BASE}/payables/aging", params={"as_of": "2026-10-19"}).json()
    parts = next(r for r in earlier["suppliers"] if r["supplier_name"] == "Example Parts")
    assert (parts["current_minor"], parts["days_1_30_minor"], parts["bill_count"]) == (0, 2000, 2)


def test_export_and_erasure_cover_bills(shop: Any, registry: Registry) -> None:
    row = bill(shop, notes="Delivered for Example Person")
    pay(shop, row, 100)
    ctx = ctx_for(shop["business"])
    with transaction(ctx) as db:
        counts = registry.erasure_handlers["purchasing"](db, ctx, ErasureTarget(uuid4(), "Example Person", (), ()))
        exported = registry.export_handlers["purchasing"](db, ctx)
    assert counts["supplier_bills_redacted"] == 1
    assert exported["supplier_bills"][0]["notes"] == "Delivered for [erased]"
    assert len(exported["supplier_bill_payments"]) == 1
