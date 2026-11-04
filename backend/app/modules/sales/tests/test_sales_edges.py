"""Transactional failure, numbering boundaries, pagination and legal-retention coverage."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Engine, text

from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.db import transaction
from app.platform.registry import ErasureTarget, Registry
from app.platform.testing.auth import ctx_for

from .. import lifecycle
from .factories import BASE, Shop


def test_failed_confirmation_has_no_number_or_partial_reservations(shop: Shop, owner_engine: Engine) -> None:
    scarce = shop.item("Cable kit", "product", 1200, is_stock_tracked=True)
    order = shop.create(
        "orders",
        lines=[
            {"catalog_item_id": shop.part, "quantity": "1"},
            {"catalog_item_id": scarce, "quantity": "1"},
        ],
    )
    failed = shop.owner.post(f"{BASE}/orders/{order['id']}/confirm", json={"version": order["version"]})
    assert failed.status_code == 409 and failed.json()["code"] == "inventory.insufficient_stock"
    draft = shop.owner.get(f"{BASE}/orders/{order['id']}").json()
    assert draft["number"] is None and draft["status"] == "draft" and draft["version"] == order["version"]
    assert Decimal(shop.available()["reserved"]) == 0
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from document_sequences where doc_type='SO'")) == 0
    good = shop.action("orders", shop.create("orders"), "confirm")
    assert good["number"] == "SO-2026-0001"


def test_numbering_is_per_type_tenant_and_business_year(shop: Shop) -> None:
    first = shop.action("quotes", shop.create("quotes"), "send")
    invoice = shop.action("invoices", shop.create(), "issue")
    assert first["number"] == "QUO-2026-0001" and invoice["number"] == "INV-2026-0001"
    clock.set_clock(lambda: datetime(2027, 1, 1, tzinfo=UTC))
    shop.owner = login_as(shop.app, shop.business, ["owner"])
    assert shop.action("invoices", shop.create(), "issue")["number"] == "INV-2026-0002"
    # Denver's local year changes seven hours after UTC on this date.
    clock.set_clock(lambda: datetime(2027, 1, 1, 7, tzinfo=UTC))
    shop.owner = login_as(shop.app, shop.business, ["owner"])
    assert shop.action("invoices", shop.create(), "issue")["number"] == "INV-2027-0001"
    other = Shop(shop.app, make_business("example-second-shop", currency="GBP"))
    other_doc = other.create(lines=[{"name": "Labour", "quantity": "1", "unit_price_minor": 100}])
    assert other_doc["currency"] == "GBP"
    assert other.action("invoices", other_doc, "issue")["number"] == "INV-2027-0001"


def test_quote_terminal_statuses_and_pagination(shop: Shop) -> None:
    for action, status in (("decline", "declined"), ("expire", "expired")):
        sent = shop.action("quotes", shop.create("quotes"), "send")
        terminal = shop.action("quotes", sent, action)
        assert terminal["status"] == status
        attempt = shop.owner.post(f"{BASE}/quotes/{terminal['id']}/accept", json={"version": terminal["version"]})
        assert attempt.status_code == 409 and attempt.json()["code"] == "sales.invalid_transition"
    one = shop.owner.get(f"{BASE}/quotes", params={"limit": 1}).json()
    assert len(one["items"]) == 1 and one["next_cursor"]
    two = shop.owner.get(f"{BASE}/quotes", params={"limit": 1, "cursor": one["next_cursor"]}).json()
    assert two["next_cursor"] is None and two["items"][0]["id"] != one["items"][0]["id"]


def test_csv_period_includes_void_original_paid_and_credit_but_no_drafts(shop: Shop) -> None:
    shop.create()
    original = shop.action("invoices", shop.create(), "issue")
    credit = shop.action("invoices", original, "credit")
    paid = shop.action("invoices", shop.create(), "issue")
    shop.action("invoices", paid, "mark-paid")
    r = shop.owner.get(f"{BASE}/invoices/export.csv", params={"from": "2026-10-28", "to": "2026-10-28"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert {row["number"] for row in rows} == {original["number"], credit["number"], paid["number"]}
    assert {row["status"] for row in rows} == {"issued", "paid", "void"}
    assert next(row for row in rows if row["kind"] == "credit_note")["total"] == "63.00"
    empty = shop.owner.get(f"{BASE}/invoices/export.csv", params={"from": "2026-10-29", "to": "2026-10-29"})
    assert list(csv.DictReader(io.StringIO(empty.text))) == []


def test_erasure_preserves_paid_void_and_credit_and_redacts_orders(shop: Shop) -> None:
    order = shop.create("orders")
    original = shop.action("invoices", shop.create(), "issue")
    credit = shop.action("invoices", original, "credit")
    paid = shop.action("invoices", shop.create(), "issue")
    paid = shop.action("invoices", paid, "mark-paid")
    target = ErasureTarget(uuid.UUID(shop.customer), "Ada Example", ("ada@example.com",), ())
    ctx = ctx_for(shop.business)
    with transaction(ctx) as db:
        lifecycle.erase(db, ctx, target)
    for doc in (original, credit, paid):
        retained = shop.owner.get(f"{BASE}/invoices/{doc['id']}").json()
        assert retained["bill_to_name"] == "Ada Example" and retained["bill_to_address"] == "1 Example Lane"
    assert shop.owner.get(f"{BASE}/orders/{order['id']}").json()["bill_to_name"] == "[erased]"


def test_demo_seed_has_sent_quote_and_issued_invoice(registry: Registry) -> None:
    from app.main import create_app
    from app.modules.identity.testing import login_as
    from app.platform.ids import fixture_id
    from app.seed import seed

    seed()
    owner = login_as(create_app(registry), fixture_id("biz-cycle"), ["owner"])
    quotes = owner.get(f"{BASE}/quotes").json()["items"]
    invoices = owner.get(f"{BASE}/invoices").json()["items"]
    assert len(quotes) == 1 and len(invoices) == 2  # Ada's, and the overdue one of the payment follow-up demo
    ada = next(i for i in invoices if i["bill_to_name"] == "Ada Example")
    assert quotes[0]["status"] == "sent" and ada["status"] == "issued"
    for doc in (quotes[0], ada):
        assert doc["bill_to_name"] == "Ada Example" and doc["bill_to_email"] == "ada@example.com"
        assert [line["name"] for line in doc["lines"]] == ["Brake service", "Brake pads (pair)"]
        assert doc["subtotal_minor"] == doc["total_minor"] == 9000
    assert ada["due_date"] is None  # Ada's invoice is not overdue: it has no due date
    overdue = owner.get(f"{BASE}/invoices", params={"overdue": "true"}).json()["items"]
    assert [(i["bill_to_name"], i["bill_to_email"], i["status"], i["kind"]) for i in overdue] == [
        ("Example Courier Co.", "fleet@example.com", "issued", "invoice")
    ]
