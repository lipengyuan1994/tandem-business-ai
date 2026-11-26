"""Partial payments, voids, mark-paid as a payment, and receivables aging."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock

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


def credit(shop: Shop, doc: dict[str, Any], **body: Any) -> Any:
    return shop.owner.post(f"{BASE}/invoices/{doc['id']}/credit", json={"version": doc["version"], **body})


def get_invoice(shop: Shop, doc_id: str) -> dict[str, Any]:
    r = shop.owner.get(f"{BASE}/invoices/{doc_id}")
    assert r.status_code == 200, r.text
    return dict(r.json())


REFUND = {"paid_on": "2026-10-28", "method": "card", "reference": " R-1 "}


def test_crediting_a_paid_invoice_in_full_refunds_what_was_paid(shop: Shop) -> None:
    doc = pay(shop, issued(shop), 1000).json()
    r = credit(shop, doc)
    assert r.status_code == 409 and r.json()["code"] == "sales.refund_required"
    assert r.json()["refund_minor"] == 1000
    note = credit(shop, doc, refund=REFUND)
    assert note.status_code == 201, note.text
    assert note.json()["kind"] == "credit_note" and note.json()["total_minor"] == TOTAL
    inv = get_invoice(shop, doc["id"])
    assert inv["status"] == "void" and inv["balance_minor"] is None
    assert (inv["amount_paid_minor"], inv["amount_credited_minor"], inv["amount_refunded_minor"]) == (1000, TOTAL, 1000)
    [refund] = inv["refunds"]
    assert (refund["amount_minor"], refund["method"], refund["reference"]) == (1000, "card", "R-1")
    assert refund["credit_note_id"] == note.json()["id"]
    assert [c["id"] for c in inv["credit_notes"]] == [note.json()["id"]]
    # The refunded payment can no longer be voided: the money already went back to the customer.
    void = shop.owner.post(
        f"{BASE}/invoices/{doc['id']}/payments/{inv['payments'][0]['id']}/void", json={"version": inv["version"]}
    )
    assert void.status_code == 409


def test_partial_credits_by_line_until_everything_is_credited(shop: Shop) -> None:
    doc = issued(shop)
    service, part = doc["lines"]
    # 45.00 of the brake service while nothing is paid: no refund, the balance drops.
    first = credit(shop, doc, lines=[{"line_id": service["id"], "quantity": "1"}], notes="Goodwill")
    assert first.status_code == 201, first.text
    assert first.json()["total_minor"] == 4500 and first.json()["notes"] == "Goodwill"
    assert first.json()["lines"][0]["credited_line_id"] == service["id"]
    inv = get_invoice(shop, doc["id"])
    assert (inv["status"], inv["balance_minor"], inv["payment_status"]) == ("issued", TOTAL - 4500, "unpaid")
    assert [line["credited_quantity"] for line in inv["lines"]] == ["1.000", "0.000"]
    # The same line cannot be credited again; an unknown line is refused.
    again = credit(shop, inv, lines=[{"line_id": service["id"], "quantity": "1"}])
    assert again.status_code == 409 and again.json()["code"] == "sales.credit_exceeds_line"
    unknown = credit(shop, inv, lines=[{"line_id": str(uuid.uuid4()), "quantity": "1"}])
    assert unknown.status_code == 422
    # Pay what is left, then credit the pads: the customer is owed 18.00 back.
    inv = pay(shop, inv, TOTAL - 4500).json()
    assert inv["status"] == "paid"
    no_refund = credit(shop, inv, lines=[{"line_id": part["id"], "quantity": "1"}])
    assert no_refund.status_code == 409 and no_refund.json()["refund_minor"] == 1800
    second = credit(shop, inv, lines=[{"line_id": part["id"], "quantity": "1"}], refund=REFUND)
    assert second.status_code == 201, second.text
    inv = get_invoice(shop, doc["id"])
    assert inv["status"] == "void" and inv["amount_credited_minor"] == TOTAL and inv["amount_refunded_minor"] == 1800
    assert len(inv["credit_notes"]) == 2
    nothing = credit(shop, inv)
    assert nothing.status_code == 409


def test_partial_credit_of_a_paid_invoice_keeps_it_paid(shop: Shop) -> None:
    doc = pay(shop, issued(shop), TOTAL).json()
    part = doc["lines"][1]
    r = credit(shop, doc, lines=[{"line_id": part["id"], "quantity": "1"}], refund=REFUND)
    assert r.status_code == 201, r.text
    inv = get_invoice(shop, doc["id"])
    assert (inv["status"], inv["balance_minor"], inv["amount_refunded_minor"]) == ("paid", 0, 1800)


def test_part_paid_invoice_credit_takes_the_open_balance_first(shop: Shop) -> None:
    doc = pay(shop, issued(shop), 5000).json()  # 13.00 still owed
    service = doc["lines"][0]
    # 45.00 credit: 13.00 clears the balance, 32.00 goes back to the customer.
    r = credit(shop, doc, lines=[{"line_id": service["id"], "quantity": "1"}], refund=REFUND)
    assert r.status_code == 201, r.text
    inv = get_invoice(shop, doc["id"])
    assert (inv["status"], inv["balance_minor"], inv["amount_refunded_minor"]) == ("paid", 0, 3200)
    # A refund is refused when the credit fits what is still owed.
    fresh = issued(shop)
    fits = credit(shop, fresh, lines=[{"line_id": fresh["lines"][1]["id"], "quantity": "1"}], refund=REFUND)
    assert fits.status_code == 422 and fits.json()["code"] == "sales.refund_not_needed"


def test_fractional_credit_never_exceeds_the_line(shop: Shop) -> None:
    # 3 x 3.33 = 9.99; credited 1 + 1 + 1 the last part takes exactly what is left.
    lines = [{"name": "Wipers", "quantity": "3", "unit_price_minor": 333, "tax_rate": "0.0725"}]
    doc = shop.action("invoices", shop.create(lines=lines), "issue")
    line = doc["lines"][0]
    totals = []
    for _ in range(3):
        doc = get_invoice(shop, doc["id"])
        r = credit(shop, doc, lines=[{"line_id": line["id"], "quantity": "1"}])
        assert r.status_code == 201, r.text
        totals.append(r.json()["total_minor"])
    assert sum(totals) == doc["total_minor"]
    assert get_invoice(shop, doc["id"])["status"] == "void"


def test_aging_counts_credits_and_refunds_by_date(shop: Shop) -> None:
    doc = issued(shop, "2026-11-30")
    r = credit(shop, doc, lines=[{"line_id": doc["lines"][1]["id"], "quantity": "1"}])
    assert r.status_code == 201, r.text
    assert aging(shop)["totals"][0]["current_minor"] == TOTAL - 1800
    assert aging(shop, "2026-10-27")["customers"] == []  # issued on the 28th


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


def test_refund_requires_dated_cash_and_aging_waits_for_associated_credit(shop: Shop) -> None:
    original = issued(shop)
    clock.set_clock(lambda: datetime(2026, 11, 1, 18, tzinfo=UTC))
    shop.owner = login_as(shop.app, shop.business, ["owner"])
    # A legitimate deposit may predate issue; a later installment cannot fund an earlier refund.
    doc = pay(shop, original, 1000, paid_on="2026-10-27").json()
    doc = pay(shop, doc, TOTAL - 1000, paid_on="2026-10-31").json()
    lines = [{"line_id": doc["lines"][1]["id"], "quantity": "1"}]

    invalid = credit(shop, doc, lines=lines, refund={**REFUND, "paid_on": "2026-10-29"})
    assert invalid.status_code == 409 and invalid.json()["code"] == "sales.refund_exceeds_dated_payments"
    assert aging(shop, "2026-10-29")["totals"][0]["total_minor"] == 5300
    valid = credit(shop, doc, lines=lines, refund={**REFUND, "paid_on": "2026-10-31"})
    assert valid.status_code == 201, valid.text
    assert aging(shop, "2026-10-31")["customers"] == []
    assert aging(shop, "2026-11-01")["customers"] == []


def test_refund_cannot_reuse_previously_refunded_cash_or_void_supporting_payment(shop: Shop) -> None:
    lines = [{"name": "Example parts", "quantity": "10", "unit_price_minor": 1000}]
    invoice = shop.action("invoices", shop.create(lines=lines), "issue")
    clock.set_clock(lambda: datetime(2026, 11, 1, 18, tzinfo=UTC))
    shop.owner = login_as(shop.app, shop.business, ["owner"])
    invoice = pay(shop, invoice, 3000, paid_on="2026-10-27").json()
    invoice = pay(shop, invoice, 7000, paid_on="2026-10-31").json()
    part = [{"line_id": invoice["lines"][0]["id"], "quantity": "2"}]
    r = credit(shop, invoice, lines=part, refund={**REFUND, "paid_on": "2026-10-29"})
    assert r.status_code == 201, r.text
    invoice = get_invoice(shop, invoice["id"])
    r = credit(shop, invoice, lines=part, refund={**REFUND, "paid_on": "2026-10-28"})
    assert r.status_code == 409 and r.json()["code"] == "sales.refund_exceeds_dated_payments"
    first_payment = invoice["payments"][0]["id"]
    r = shop.owner.post(
        f"{BASE}/invoices/{invoice['id']}/payments/{first_payment}/void", json={"version": invoice["version"]}
    )
    assert r.status_code == 409 and r.json()["code"] == "sales.payment_refunded"
