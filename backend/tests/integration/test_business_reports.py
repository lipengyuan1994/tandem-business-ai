"""Sales, margin, stock value and spend reports over one synthetic shop's history (Denver, USD, tax 0).

History, local dates in 2026:
- Sep 10 PO-1 approved: 10 pads at 10.00. Sep 15: all 10 received.
- Sep 25 INV-1 to Ada: 1 pad 18.00 (credited Oct 25).
- Oct 1 PO-2 approved: 10 pads at 13.00. Oct 2: 4 chains taken into stock without a cost. Oct 5: 5 pads received.
- Oct 20 INV-2 to Ada: 2 pads, 1 brake service (2 pads as parts), a 5.00 free-text line. Oct 22 INV-3 to Bob: 1 chain.
- A draft invoice never issued. Average pad cost after Oct 5: (10 x 10.00 + 5 x 13.00) / 15 = 11.00.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.modules.sales import reports as sales_reports
from app.platform import clock
from app.platform.db import transaction
from app.platform.errors import Forbidden
from app.platform.registry import Registry
from app.platform.reporting import DateRange
from app.platform.testing.auth import ctx_for

pytestmark = pytest.mark.integration

SALES = "/api/v1/sales/reports/sales"
MARGIN = "/api/v1/sales/reports/margin"
STOCK = "/api/v1/inventory/reports/stock-value"
SPEND = "/api/v1/purchasing/reports/spend"
FALL = {"date_from": "2026-09-01", "date_to": "2026-10-31"}
OCTOBER = {"date_from": "2026-10-01", "date_to": "2026-10-31"}


def at(month: int, day: int) -> None:
    """Noon in Denver on that day of 2026."""
    clock.set_clock(lambda: datetime(2026, month, day, 18, tzinfo=UTC))


def ok(r: Any, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json()


class History:
    def __init__(self, app: Any, business: uuid.UUID) -> None:
        self.app, self.business = app, business
        self.at(9, 1)
        c = self.owner
        self.ada = ok(c.post("/api/v1/crm/customers", json={"display_name": "Ada Example"}), 201)["id"]
        self.bob = ok(c.post("/api/v1/crm/customers", json={"display_name": "Bob Example"}), 201)["id"]
        self.pads = self.item("Brake pads", "product", 1800, is_stock_tracked=True)
        self.chain = self.item("Chain", "product", 3000, is_stock_tracked=True)
        self.brake = self.item("Brake service", "service", 4500, duration_minutes=30, resource_kind="staff")
        ok(c.put(f"/api/v1/catalog/items/{self.brake}/parts", json=[{"item_id": self.pads, "quantity": "2"}]))
        self.supplier = ok(
            c.post("/api/v1/purchasing/suppliers", json={"name": "Example Parts", "email": "parts@example.com"}), 201
        )["id"]

        self.at(9, 10)
        po1 = self.order(1000)
        self.at(9, 15)
        self.receive(po1, "10")
        self.at(9, 25)
        early = self.issue(self.ada, [{"catalog_item_id": self.pads, "quantity": "1"}])
        self.at(10, 1)
        po2 = self.order(1300)
        self.at(10, 2)
        ok(
            self.owner.post(
                "/api/v1/inventory/movements", json={"item_id": self.chain, "quantity": "4", "reason": "receipt"}
            ),
            201,
        )
        self.at(10, 5)
        self.receive(po2, "5")
        self.at(10, 20)
        lines = [
            {"catalog_item_id": self.pads, "quantity": "2"},
            {"catalog_item_id": self.brake, "quantity": "1"},
            {"name": "Shop supplies", "quantity": "1", "unit_price_minor": 500, "tax_rate": "0"},
        ]
        self.issue(self.ada, lines)
        self.at(10, 22)
        self.issue(self.bob, [{"catalog_item_id": self.chain, "quantity": "1"}])
        self.draft(self.bob, [{"catalog_item_id": self.chain, "quantity": "5"}])
        self.at(10, 25)
        early = ok(self.owner.get(f"/api/v1/sales/invoices/{early['id']}"))
        body = {"version": early["version"]}
        ok(self.owner.post(f"/api/v1/sales/invoices/{early['id']}/credit", json=body), 201)
        self.at(10, 28)

    def at(self, month: int, day: int) -> None:
        """Move the clock, then sign in again: sessions expire across the jump."""
        at(month, day)
        self.owner = login_as(self.app, self.business, ["owner"])

    def item(self, name: str, kind: str, price: int, **fields: Any) -> str:
        body = {"name": name, "kind": kind, "price_minor": price, **fields}
        return str(ok(self.owner.post("/api/v1/catalog/items", json=body), 201)["id"])

    def draft(self, customer: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
        return dict(ok(self.owner.post("/api/v1/sales/invoices", json={"customer_id": customer, "lines": lines}), 201))

    def issue(self, customer: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
        doc = self.draft(customer, lines)
        r = self.owner.post(f"/api/v1/sales/invoices/{doc['id']}/issue", json={"version": doc["version"]})
        return dict(ok(r))

    def order(self, unit_cost: int) -> dict[str, Any]:
        base = "/api/v1/purchasing/purchase-orders"
        body = {
            "supplier_id": self.supplier,
            "lines": [{"catalog_item_id": self.pads, "quantity": "10", "unit_cost_minor": unit_cost}],
        }
        po = ok(self.owner.post(base, json=body), 201)
        for action in ("approve", "send"):
            po = ok(self.owner.post(f"{base}/{po['id']}/{action}", json={"version": po["version"]}))
        return dict(po)

    def receive(self, po: dict[str, Any], quantity: str) -> None:
        body = {"version": po["version"], "lines": [{"po_line_id": po["lines"][0]["id"], "quantity": quantity}]}
        ok(self.owner.post(f"/api/v1/purchasing/purchase-orders/{po['id']}/receipts", json=body), 201)


@pytest.fixture
def shop(registry: Registry) -> History:
    return History(create_app(registry), make_business())


def rows(report: dict[str, Any], *fields: str) -> list[tuple[Any, ...]]:
    return [tuple(r[f] for f in fields) for r in report["rows"]]


def test_sales_by_period_customer_and_item_net_of_credit_notes(shop: History) -> None:
    c = shop.owner
    by_month = ok(c.get(SALES, params={**FALL, "group_by": "period", "period": "month"}))
    assert rows(by_month, "key", "invoice_count", "credit_note_count", "net_minor") == [
        ("2026-09-01", 1, 0, 1800),
        ("2026-10-01", 2, 1, 9800),  # 8600 + 3000 - 1800 credit; the draft never counts
    ]
    assert by_month["totals"] == [
        {
            "currency": "USD",
            "invoice_count": 3,
            "credit_note_count": 1,
            "net_minor": 11600,
            "tax_minor": 0,
            "total_minor": 11600,
        }
    ]

    by_week = ok(c.get(SALES, params={**OCTOBER, "group_by": "period", "period": "week"}))
    assert rows(by_week, "key", "net_minor") == [("2026-10-19", 9800)]  # weeks start on Monday; Oct 20-25

    by_customer = ok(c.get(SALES, params={**FALL, "group_by": "customer"}))
    assert rows(by_customer, "key", "label", "invoice_count", "credit_note_count", "net_minor") == [
        (shop.ada, "Ada Example", 2, 1, 8600),
        (shop.bob, "Bob Example", 1, 0, 3000),
    ]

    by_item = ok(c.get(SALES, params={**FALL, "group_by": "item"}))
    assert rows(by_item, "key", "label", "quantity", "net_minor", "invoice_count", "credit_note_count") == [
        (shop.brake, "Brake service", "1.000", 4500, 1, 0),
        (shop.pads, "Brake pads", "2.000", 3600, 2, 1),
        (shop.chain, "Chain", "1.000", 3000, 1, 0),
        ("free_text", "", "1.000", 500, 1, 0),
    ]
    assert by_item["totals"][0]["invoice_count"] == 3 and by_item["totals"][0]["net_minor"] == 11600


def test_margin_uses_average_purchase_cost_and_keeps_uncosted_revenue_apart(shop: History) -> None:
    by_item = ok(shop.owner.get(MARGIN, params={**OCTOBER, "group_by": "item"}))
    assert by_item["currency"] == "USD" and by_item["cost_basis"] == "average_purchase_cost"
    fields = ("key", "kind", "revenue_minor", "cost_minor", "margin_minor", "margin_basis_points")
    assert rows(by_item, *fields) == [
        (shop.brake, "service", 4500, 2200, 2300, 5111),  # 2 pads at 11.00, materials only
        (shop.chain, "product", 3000, None, None, None),  # taken into stock without a cost
        (shop.pads, "product", 1800, 1100, 700, 3889),  # 2 sold, 1 credited from September
        ("free_text", "free_text", 500, None, None, None),
    ]
    assert by_item["totals"] == {
        "revenue_minor": 9800,
        "costed_revenue_minor": 6300,
        "uncosted_revenue_minor": 3500,
        "cost_minor": 3300,
        "margin_minor": 3000,
        "margin_basis_points": 4762,
    }
    assert by_item["other_currency_document_count"] == 0

    by_kind = ok(shop.owner.get(MARGIN, params={**OCTOBER, "group_by": "kind"}))
    assert rows(by_kind, "key", "revenue_minor", "costed_revenue_minor", "cost_minor") == [
        ("product", 4800, 1800, 1100),
        ("service", 4500, 4500, 2200),
        ("free_text", 500, 0, None),
    ]

    # Before the October receipt the average pad cost was 10.00.
    september = ok(shop.owner.get(MARGIN, params={"date_from": "2026-09-01", "date_to": "2026-09-30"}))
    assert rows(september, "key", "revenue_minor", "cost_minor") == [(shop.pads, 1800, 1000)]


def test_stock_value_now_in_the_past_and_by_location(shop: History) -> None:
    now = ok(shop.owner.get(STOCK))
    assert now["as_of"] == "2026-10-28" and now["currency"] == "USD"
    assert rows(now, "key", "quantity", "unit_cost_minor", "value_minor", "uncosted_item_count") == [
        (shop.pads, "15.000", 1100, 16500, 0),
        (shop.chain, "4.000", None, 0, 1),
    ]
    assert now["totals"] == {"value_minor": 16500, "item_count": 2, "uncosted_item_count": 1}

    past = ok(shop.owner.get(STOCK, params={"as_of": "2026-09-20"}))
    assert rows(past, "key", "quantity", "unit_cost_minor", "value_minor") == [(shop.pads, "10.000", 1000, 10000)]

    by_location = ok(shop.owner.get(STOCK, params={"group_by": "location"}))
    assert rows(by_location, "label", "quantity", "value_minor", "item_count", "uncosted_item_count") == [
        ("Main stock", None, 16500, 2, 1)
    ]


def test_spend_by_supplier_and_period(shop: History) -> None:
    by_supplier = ok(shop.owner.get(SPEND, params=FALL))
    fields = ("label", "order_count", "ordered_minor", "receipt_count", "received_minor")
    assert rows(by_supplier, *fields) == [("Example Parts", 2, 23000, 2, 16500)]
    assert by_supplier["rows"][0]["key"] == shop.supplier

    by_month = ok(shop.owner.get(SPEND, params={**FALL, "group_by": "period"}))
    assert rows(by_month, "key", "ordered_minor", "received_minor") == [
        ("2026-09-01", 10000, 10000),
        ("2026-10-01", 13000, 6500),
    ]
    assert by_month["totals"] == [
        {"currency": "USD", "order_count": 2, "ordered_minor": 23000, "receipt_count": 2, "received_minor": 16500}
    ]

    default = ok(shop.owner.get(SPEND))  # this month so far
    assert (default["date_from"], default["date_to"]) == ("2026-10-01", "2026-10-28")
    assert rows(default, "ordered_minor", "received_minor") == [(13000, 6500)]


def test_permissions_ranges_and_tenant_isolation(shop: History, other_business: uuid.UUID) -> None:
    marketing = login_as(shop.app, shop.business, ["marketing_manager"])
    for url in (SALES, MARGIN, STOCK, SPEND):
        assert marketing.get(url).status_code == 403, url
    inventory = login_as(shop.app, shop.business, ["inventory_manager"])
    assert [inventory.get(url).status_code for url in (SALES, MARGIN, STOCK, SPEND)] == [403, 403, 200, 200]
    auditor = login_as(shop.app, shop.business, ["auditor"])
    assert all(auditor.get(url).status_code == 200 for url in (SALES, MARGIN, STOCK, SPEND))

    # Costs are purchase prices: margin needs purchasing read on top of sales read.
    ctx = ctx_for(shop.business, frozenset({"sales.document.read"}))
    with pytest.raises(Forbidden), transaction(ctx) as db:
        sales_reports.margin(db, ctx, DateRange(datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date()), "item")

    for url in (SALES, SPEND, MARGIN):
        r = shop.owner.get(url, params={"date_from": "2026-10-02", "date_to": "2026-10-01"})
        assert r.status_code == 422 and r.json()["code"] == "reports.invalid_range", url
        r = shop.owner.get(url, params={"date_from": "2020-01-01", "date_to": "2026-10-01"})
        assert r.status_code == 422 and r.json()["code"] == "reports.range_too_long", url
    assert shop.owner.get(SALES, params={"group_by": "region"}).status_code == 422

    other: TestClient = login_as(shop.app, other_business, ["owner"])
    for url, params in ((SALES, FALL), (MARGIN, FALL), (STOCK, {}), (SPEND, FALL)):
        report = ok(other.get(url, params=params))
        assert report["rows"] == [], url
