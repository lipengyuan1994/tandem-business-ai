"""Synthetic sales shop built through existing staff APIs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.modules.identity.testing import login_as

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
BASE = "/api/v1/sales"


class Shop:
    def __init__(self, app: Any, business: uuid.UUID) -> None:
        self.app, self.business = app, business
        self.owner = login_as(app, business, ["owner"])
        r = self.owner.post(
            "/api/v1/crm/customers",
            json={
                "display_name": "Ada Example",
                "contact_points": [{"type": "email", "value": "ada@example.com"}],
            },
        )
        assert r.status_code == 201, r.text
        self.customer = r.json()["id"]
        self.part = self.item("Brake pads", "product", 1800, is_stock_tracked=True)
        self.brake = self.item("Brake service", "service", 4500, duration_minutes=30, resource_kind="staff")
        r = self.owner.post(
            "/api/v1/inventory/movements",
            json={
                "item_id": self.part,
                "quantity": "10",
                "reason": "receipt",
            },
        )
        assert r.status_code == 201, r.text

    def item(self, name: str, kind: str, price: int, **fields: Any) -> str:
        r = self.owner.post(
            "/api/v1/catalog/items",
            json={
                "name": name,
                "kind": kind,
                "price_minor": price,
                **fields,
            },
        )
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    def create(self, family: str = "invoices", lines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        r = self.owner.post(
            f"{BASE}/{family}",
            json={
                "customer_id": self.customer,
                "bill_to_address": "1 Example Lane",
                "notes": "For Ada Example",
                "lines": lines
                or [
                    {"catalog_item_id": self.brake, "quantity": "1"},
                    {"catalog_item_id": self.part, "quantity": "1"},
                ],
            },
        )
        assert r.status_code == 201, r.text
        return dict(r.json())

    def action(self, family: str, doc: dict[str, Any], action: str) -> dict[str, Any]:
        r = self.owner.post(f"{BASE}/{family}/{doc['id']}/{action}", json={"version": doc["version"]})
        assert r.status_code in (200, 201), r.text
        return dict(r.json())

    def available(self) -> dict[str, Any]:
        r = self.owner.get("/api/v1/inventory/levels")
        assert r.status_code == 200, r.text
        return next(x for x in r.json() if x["item_id"] == self.part)
