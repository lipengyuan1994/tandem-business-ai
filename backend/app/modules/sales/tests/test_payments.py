"""Partial payments, voids, mark-paid as a payment, and receivables aging."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Engine, text

from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business

from .factories import BASE, Shop

# Shop.create(): brake service 45.00 + brake pads 18.00, default tax 0 -> total 6300.
TOTAL = 6300


def issued(shop: Shop, due: str | None = "2026-11-15") -> dict[str, Any]:
    draft = shop.create()
    r = shop.owner.patch(f"{BASE}/invoices/{draft['id']}", json={"version": draft["version"], "due_date": due})
    assert r.status_code == 200, r.text
    return shop.action("invoices", r.json(), "issue")


def pay(shop: Shop, doc: dict[str, Any], amount: int, **over: Any) -> Any:
    body = {"version": doc["version"], "amount_minor": amount, "paid_on": "2026-10-28", "method": "card", **over}
    return shop.owner.post(f"{BASE}/invoices/{doc['id']}/payments", json=body)


def test_partial_then_full_payment_updates_balance_status_and_history(shop: Shop) -> None:
    doc = issued(shop)
    assert doc["total_minor"] == TOTAL
    assert (doc["payment_status"], doc["amount_paid_minor"], doc["balance_minor"]) == ("unpaid", 0, TOTAL)

    r = pay(shop, doc, 2000, reference="  AUTH-1  ")
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "issued"
    assert (doc["payment_status"], doc["amount_paid_minor"], doc["balance_minor"]) == ("partially_paid", 2000, 4300)
    assert doc["paid_at"] is None
    assert [(p["amount_minor"], p["method"], p["reference"]) for p in doc["payments"]] == [(2000, "card", "AUTH-1")]

    r = pay(shop, doc, 4300, method="cash", reference="")
    assert r.status_code == 201, r.text
    doc = r.json()
    assert (doc["status"], doc["payment_status"], doc["balance_minor"]) == ("paid", "paid", 0)
    assert doc["paid_at"] is not None
    assert [p["reference"] for p in doc["payments"]] == ["AUTH-1", None]

    assert pay(shop, doc, 1).status_code == 409  # paid invoices take no more payments


def test_overpayment_stale_version_future_date_and_bad_input_are_rejected(shop: Shop) -> None:
    doc = issued(shop)
    r = pay(shop, doc, TOTAL + 1)
    assert r.status_code == 409 and r.json()["code"] == "sales.overpayment", r.text
    assert r.json()["balance_minor"] == TOTAL
    assert pay(shop, {**doc, "version": doc["version"] - 1}, 100).json()["code"] == "sales.version_conflict"
    r = pay(shop, doc, 100, paid_on="2026-10-29")  # NOW is 2026-10-28 in Denver
    assert r.status_code == 422 and r.json()["code"] == "sales.payment_date_in_future"
    for bad in ({"amount_minor": 0}, {"amount_minor": "10"}, {"method": "crypto"}, {"reference": "x" * 201}):
        assert pay(shop, doc, 100, **bad).status_code == 422, bad
    draft = shop.create()
    r = pay(shop, draft, 100)
    assert r.status_code == 409 and r.json()["code"] == "sales.invalid_transition"


def test_mark_paid_records_the_open_balance_as_a_payment(shop: Shop) -> None:
    doc = pay(shop, issued(shop), 1500).json()
    doc = shop.action("invoices", doc, "mark-paid")
    assert (doc["status"], doc["amount_paid_minor"], doc["balance_minor"]) == ("paid", TOTAL, 0)
    assert [(p["amount_minor"], p["method"]) for p in doc["payments"]] == [(1500, "card"), (4800, "other")]


def test_void_payment_reopens_the_invoice_and_needs_admin(shop: Shop) -> None:
    doc = pay(shop, issued(shop), TOTAL).json()
    assert doc["status"] == "paid"
    payment = doc["payments"][0]["id"]
    operator = login_as(shop.app, shop.business, ["operator"])
    url = f"{BASE}/invoices/{doc['id']}/payments/{payment}/void"
    assert operator.post(url, json={"version": doc["version"]}).status_code == 403
    r = shop.owner.post(url, json={"version": doc["version"]})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert (doc["status"], doc["payment_status"], doc["balance_minor"], doc["paid_at"]) == (
        "issued",
        "unpaid",
        TOTAL,
        None,
    )
    assert doc["payments"][0]["voided_at"] is not None
    again = shop.owner.post(url, json={"version": doc["version"]})
    assert again.status_code == 409 and again.json()["code"] == "sales.payment_already_voided"
    missing = shop.owner.post(f"{BASE}/invoices/{doc['id']}/payments/{uuid.uuid4()}/void", json={"version": 1})
    assert missing.status_code in (404, 409)


def test_operator_records_payments_auditor_cannot(shop: Shop) -> None:
    doc = issued(shop)
    auditor = login_as(shop.app, shop.business, ["auditor"])
    body = {"version": doc["version"], "amount_minor": 100, "paid_on": "2026-10-28", "method": "cash"}
    assert auditor.post(f"{BASE}/invoices/{doc['id']}/payments", json=body).status_code == 403
    operator = login_as(shop.app, shop.business, ["operator"])
    assert operator.post(f"{BASE}/invoices/{doc['id']}/payments", json=body).status_code == 201


def test_invoices_with_payments_cannot_be_credited(shop: Shop) -> None:
    doc = pay(shop, issued(shop), 100).json()
    r = shop.owner.post(f"{BASE}/invoices/{doc['id']}/credit", json={"version": doc["version"]})
    assert r.status_code == 409 and r.json()["code"] == "sales.invoice_has_payments"


def test_payments_are_tenant_scoped(shop: Shop) -> None:
    doc = issued(shop)
    other = Shop(shop.app, make_business("payments-other"))
    body = {"version": doc["version"], "amount_minor": 100, "paid_on": "2026-10-28", "method": "cash"}
    assert other.owner.post(f"{BASE}/invoices/{doc['id']}/payments", json=body).status_code == 404


def test_reminder_amount_is_the_open_balance(shop: Shop) -> None:
    from app.modules.sales.public import list_overdue_invoices
    from app.platform.db import transaction
    from app.platform.testing.auth import ctx_for

    doc = pay(shop, issued(shop, "2026-10-01"), 2500).json()
    ctx = ctx_for(shop.business, ["sales.document.read"])
    with transaction(ctx) as db:
        [row] = list_overdue_invoices(db, ctx)
    assert (row.id, row.total_minor, row.balance_minor) == (uuid.UUID(doc["id"]), TOTAL, TOTAL - 2500)


def aging(shop: Shop, as_of: str | None = None) -> dict[str, Any]:
    r = shop.owner.get(f"{BASE}/receivables/aging", params={"as_of": as_of} if as_of else None)
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_aging_buckets_by_days_past_due_per_customer(shop: Shop) -> None:
    # Issue dates are all 2026-10-28 (frozen clock); due dates set the bucket.
    issued(shop, "2026-12-01")
    issued(shop, None)  # no due date: ages from the issue date, which is today -> current
    pay(shop, issued(shop, "2026-10-18"), 1000)
    issued(shop, "2026-09-28")  # 30 days -> 1-30
    issued(shop, "2026-09-27")  # 31 days -> 31-60
    issued(shop, "2026-07-30")  # 90 days -> 61-90
    issued(shop, "2026-07-29")  # 91 days -> 90+
    paid = issued(shop, "2026-07-01")
    shop.action("invoices", paid, "mark-paid")
    shop.create()  # draft: not receivable

    report = aging(shop)
    assert report["as_of"] == "2026-10-28"
    [row] = report["customers"]
    assert row["customer_name"] == "Ada Example" and row["currency"] == "USD"
    assert row["current_minor"] == 2 * TOTAL
    assert row["days_1_30_minor"] == 2 * TOTAL - 1000
    assert row["days_31_60_minor"] == TOTAL
    assert row["days_61_90_minor"] == TOTAL
    assert row["days_over_90_minor"] == TOTAL
    assert row["total_minor"] == 7 * TOTAL - 1000
    assert row["invoice_count"] == 7
    [total] = report["totals"]
    assert total == {k: v for k, v in row.items() if k in total}


def test_aging_as_of_a_past_date_ignores_later_issues_and_payments(shop: Shop) -> None:
    doc = issued(shop, "2026-10-20")
    pay(shop, doc, 1000, paid_on="2026-10-27")
    before = aging(shop, "2026-10-26")
    assert before["customers"] == [] and before["totals"] == []  # issued on 10-28, after the as-of date
    after = aging(shop, "2026-12-31")
    assert after["customers"][0]["days_61_90_minor"] == TOTAL - 1000


def test_aging_excludes_voided_payments_and_other_businesses(shop: Shop) -> None:
    doc = pay(shop, issued(shop, "2026-11-30"), TOTAL).json()
    assert aging(shop)["customers"] == []
    r = shop.owner.post(
        f"{BASE}/invoices/{doc['id']}/payments/{doc['payments'][0]['id']}/void", json={"version": doc["version"]}
    )
    assert r.status_code == 200, r.text
    assert aging(shop)["totals"][0]["current_minor"] == TOTAL
    other = Shop(shop.app, make_business("aging-other"))
    assert other.owner.get(f"{BASE}/receivables/aging").json()["customers"] == []
    auditor = login_as(shop.app, shop.business, ["auditor"])
    assert auditor.get(f"{BASE}/receivables/aging").status_code == 200


def test_payments_table_is_exported_and_amount_paid_is_bounded(shop: Shop, owner_engine: Engine) -> None:
    from app.modules.sales.lifecycle import TABLES
    from app.modules.sales.models import InvoicePayment

    assert InvoicePayment in TABLES
    doc = pay(shop, issued(shop), 100).json()
    with owner_engine.connect() as conn:
        try:
            conn.execute(
                text("update invoices set amount_paid_minor = total_minor + 1 where id = :id"), {"id": doc["id"]}
            )
        except Exception as exc:
            assert "ck_invoices_amount_paid" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("the database accepted a paid amount above the total")
