from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from typing import Any

from app.modules.identity.testing import login_as
from app.platform import jobs
from app.platform.registry import Registry

from .fake_quickbooks import REALM

QB = "/api/v1/accounting/quickbooks"
BASE = "/api/v1/sales"
TODAY = "2026-10-28"
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


class Shop:
    """Synthetic business with one customer and one priced service, built through the staff APIs."""

    def __init__(self, app: Any, business: Any) -> None:
        self.app, self.business = app, business
        self.owner = login_as(app, business, ["owner"])
        r = self.owner.post(
            "/api/v1/crm/customers",
            json={"display_name": "Ada Example", "contact_points": [{"type": "email", "value": "ada@example.com"}]},
        )
        assert r.status_code == 201, r.text
        self.customer = r.json()["id"]
        r = self.owner.post(
            "/api/v1/catalog/items",
            json={
                "name": "Brake service",
                "kind": "service",
                "price_minor": 4500,
                "duration_minutes": 30,
                "resource_kind": "staff",
            },
        )
        assert r.status_code == 201, r.text
        self.service = r.json()["id"]

    def create(self, quantity: str = "2", customer: str | None = None) -> dict[str, Any]:
        r = self.owner.post(
            f"{BASE}/invoices",
            json={
                "customer_id": customer or self.customer,
                "lines": [{"catalog_item_id": self.service, "quantity": quantity}],
            },
        )
        assert r.status_code == 201, r.text
        return dict(r.json())

    def action(self, family: str, doc: dict[str, Any], action: str) -> dict[str, Any]:
        r = self.owner.post(f"{BASE}/{family}/{doc['id']}/{action}", json={"version": doc["version"]})
        assert r.status_code in (200, 201), r.text
        return dict(r.json())


def connect(shop: Shop, code: str = "good-code") -> Any:
    r = shop.owner.post(f"{QB}/connect")
    assert r.status_code == 200, r.text
    state = urllib.parse.parse_qs(urllib.parse.urlparse(r.json()["authorize_url"]).query)["state"][0]
    return shop.owner.get(
        f"{QB}/callback", params={"code": code, "state": state, "realmId": REALM}, follow_redirects=False
    )


def map_accounts(shop: Shop, **over: Any) -> dict[str, Any]:
    status = shop.owner.get(QB).json()
    body = {
        "version": status["version"],
        "sync_from": TODAY,
        "auto_sync": True,
        "income_account_id": "1",
        "deposit_account_id": "2",
        "expense_account_id": "3",
        "bank_account_id": "2",
        **over,
    }
    r = shop.owner.put(f"{QB}/settings", json=body)
    assert r.status_code == 200, r.text
    return dict(r.json())


def sync(shop: Shop, registry: Registry) -> None:
    r = shop.owner.post(f"{QB}/sync")
    assert r.status_code == 202, r.text
    jobs.run_pending(registry)


def issued_invoice(shop: Shop) -> dict[str, Any]:
    return shop.action("invoices", shop.create(), "issue")


def pay(shop: Shop, doc: dict[str, Any], amount: int, **over: Any) -> dict[str, Any]:
    body = {"version": doc["version"], "amount_minor": amount, "paid_on": TODAY, "method": "card", **over}
    r = shop.owner.post(f"{BASE}/invoices/{doc['id']}/payments", json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())


def supplier_bill(shop: Shop, total: int = 12000, **over: Any) -> dict[str, Any]:
    r = shop.owner.post("/api/v1/purchasing/suppliers", json={"name": "Example Parts", "email": "parts@example.com"})
    assert r.status_code == 201, r.text
    body = {"supplier_id": r.json()["id"], "bill_date": TODAY, "total_minor": total, **over}
    r = shop.owner.post("/api/v1/purchasing/bills", json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())


def records(shop: Shop, **params: Any) -> list[dict[str, Any]]:
    r = shop.owner.get(f"{QB}/records", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return list(r.json()["items"])
