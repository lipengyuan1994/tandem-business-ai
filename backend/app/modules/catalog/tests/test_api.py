"""M05 catalog tests."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform.registry import Registry


@pytest.fixture
def biz() -> uuid.UUID:
    return make_business()


@pytest.fixture
def client(registry: Registry, biz: uuid.UUID) -> Any:
    app = create_app(registry)
    return lambda roles=("owner",), business=None: login_as(app, business or biz, list(roles))


SERVICE = {
    "kind": "service",
    "name": "Brake service",
    "duration_minutes": 60,
    "buffer_minutes": 15,
    "resource_kind": "staff",
    "price_minor": 6500,
    "is_bookable_online": True,
}
PADS = {"kind": "product", "sku": "BP-01", "name": "Brake pads (pair)", "price_minor": 2500, "is_stock_tracked": True}


def test_service_requires_duration_and_resource_kind(client: Any) -> None:
    c = client()
    bad = c.post("/api/v1/catalog/items", json={**SERVICE, "duration_minutes": None})
    assert bad.status_code == 422
    ok = c.post("/api/v1/catalog/items", json=SERVICE)
    assert ok.status_code == 201 and ok.json()["currency"] == "USD"
    product_with_duration = c.post("/api/v1/catalog/items", json={**PADS, "duration_minutes": 30})
    assert product_with_duration.status_code == 422


def test_part_must_be_stock_tracked_product(client: Any) -> None:
    c = client(["inventory_manager"])
    svc = c.post("/api/v1/catalog/items", json=SERVICE).json()
    untracked = c.post("/api/v1/catalog/items", json={**PADS, "sku": "X-1", "is_stock_tracked": False}).json()
    pads = c.post("/api/v1/catalog/items", json=PADS).json()
    bad = c.put(f"/api/v1/catalog/items/{svc['id']}/parts", json=[{"item_id": untracked["id"], "quantity": "1"}])
    assert bad.status_code == 422 and bad.json()["code"] == "catalog.invalid_part"
    ok = c.put(f"/api/v1/catalog/items/{svc['id']}/parts", json=[{"item_id": pads["id"], "quantity": "1"}])
    assert ok.status_code == 200 and ok.json()[0]["item_name"] == "Brake pads (pair)"


def test_cannot_archive_required_part_of_active_service(client: Any) -> None:
    c = client()
    svc = c.post("/api/v1/catalog/items", json=SERVICE).json()
    pads = c.post("/api/v1/catalog/items", json=PADS).json()
    c.put(f"/api/v1/catalog/items/{svc['id']}/parts", json=[{"item_id": pads["id"], "quantity": "1"}])
    blocked = c.post(f"/api/v1/catalog/items/{pads['id']}/archive", json={"version": pads["version"]})
    assert blocked.status_code == 409 and blocked.json()["code"] == "catalog.item_in_use"
    c.post(f"/api/v1/catalog/items/{svc['id']}/archive", json={"version": svc["version"]})
    assert c.post(f"/api/v1/catalog/items/{pads['id']}/archive", json={"version": pads["version"]}).status_code == 200


def test_public_services_only_lists_bookable_online_active(client: Any, registry: Registry) -> None:
    from fastapi.testclient import TestClient

    c = client()
    c.post("/api/v1/catalog/items", json=SERVICE)
    c.post("/api/v1/catalog/items", json={**SERVICE, "name": "Internal check", "is_bookable_online": False})
    c.post("/api/v1/catalog/items", json=PADS)
    public = TestClient(create_app(registry)).get("/api/v1/public/example-cycle-works/services").json()
    assert [s["name"] for s in public] == ["Brake service"] and public[0]["price"] == {
        "amount_minor": 6500,
        "currency": "USD",
    }


def test_sku_unique_version_conflict_permissions_and_isolation(client: Any) -> None:
    c = client()
    pads = c.post("/api/v1/catalog/items", json=PADS).json()
    assert c.post("/api/v1/catalog/items", json=PADS).json()["code"] == "catalog.sku_taken"
    assert (
        c.patch(
            f"/api/v1/catalog/items/{pads['id']}", json={"version": pads["version"], "price_minor": 2600}
        ).status_code
        == 200
    )
    stale = c.patch(f"/api/v1/catalog/items/{pads['id']}", json={"version": pads["version"], "price_minor": 2700})
    assert stale.status_code == 409 and stale.json()["current_version"] == pads["version"] + 1
    assert client(["operator"]).post("/api/v1/catalog/items", json={**PADS, "sku": "Z"}).status_code == 403
    assert client(["operator"]).get("/api/v1/catalog/items", params={"q": "brake"}).json()["items"][0]["sku"] == "BP-01"
    other = client(["owner"], make_business("example-garden-store"))
    assert other.get(f"/api/v1/catalog/items/{pads['id']}").status_code == 404
