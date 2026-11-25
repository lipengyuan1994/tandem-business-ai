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


@pytest.mark.parametrize("field", ["name", "buffer_minutes", "is_bookable_online"])
def test_clearing_a_required_field_is_refused_not_500(client: Any, field: str) -> None:
    c = client()
    item = c.post("/api/v1/catalog/items", json=SERVICE).json()
    r = c.patch(f"/api/v1/catalog/items/{item['id']}", json={"version": item["version"], field: None})
    assert r.status_code == 422, r.text
    assert (
        c.patch(
            f"/api/v1/catalog/items/{item['id']}", json={"version": item["version"], "description": None}
        ).status_code
        == 200
    )  # nullable fields can still be cleared


@pytest.mark.concurrency
@pytest.mark.parametrize("first", ["parts", "archive"])
def test_archive_and_parts_edit_cannot_both_win(client: Any, biz: uuid.UUID, first: str) -> None:
    """Archiving a part while someone adds it to a service used to let both commit: an active service then needed an
    archived part. Whichever starts second must now see the first one's result."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal

    from app.modules.catalog import permissions, service
    from app.modules.catalog.schemas import ServicePartIn
    from app.modules.org.testing import system_ctx
    from app.platform.db import transaction
    from app.platform.errors import Conflict, Invalid

    c = client()
    svc = c.post("/api/v1/catalog/items", json=SERVICE).json()
    pads = c.post("/api/v1/catalog/items", json=PADS).json()
    ctx = system_ctx(biz, permissions.ALL)
    done_first, release = threading.Event(), threading.Event()
    part = [ServicePartIn(item_id=uuid.UUID(pads["id"]), quantity=Decimal(1))]

    def run(action: str, hold: bool) -> str:
        try:
            with transaction(ctx) as db:
                if action == "parts":
                    service.replace_parts(db, ctx, uuid.UUID(svc["id"]), part)
                else:
                    service.archive_item(db, ctx, uuid.UUID(pads["id"]), pads["version"])
                if hold:  # keep the first transaction open while the second one starts
                    done_first.set()
                    release.wait(10)
            return "ok"
        except (Conflict, Invalid) as exc:
            return exc.code

    second = "archive" if first == "parts" else "parts"
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, first, True)
        assert done_first.wait(10)
        b = pool.submit(run, second, False)
        time.sleep(0.5)  # the second request is now waiting on the first one's lock
        release.set()
        results = {first: a.result(timeout=15), second: b.result(timeout=15)}
    expected = (
        {"parts": "ok", "archive": "catalog.item_in_use"}
        if first == "parts"
        else {"archive": "ok", "parts": "catalog.invalid_part"}
    )
    assert results == expected


def test_archived_item_can_be_restored(client: Any) -> None:
    """Archiving used to be one-way: an item archived by mistake had to be created again under a new SKU."""
    c = client()
    svc = c.post("/api/v1/catalog/items", json=SERVICE).json()
    pads = c.post("/api/v1/catalog/items", json=PADS).json()
    c.put(f"/api/v1/catalog/items/{svc['id']}/parts", json=[{"item_id": pads["id"], "quantity": "1"}])
    svc = c.post(f"/api/v1/catalog/items/{svc['id']}/archive", json={"version": svc["version"]}).json()
    pads = c.post(f"/api/v1/catalog/items/{pads['id']}/archive", json={"version": pads["version"]}).json()
    assert pads["status"] == "archived"
    # a service needs its parts active first
    blocked = c.post(f"/api/v1/catalog/items/{svc['id']}/restore", json={"version": svc["version"]})
    assert blocked.status_code == 409 and blocked.json()["code"] == "catalog.part_archived"
    assert (
        client(["auditor"])
        .post(f"/api/v1/catalog/items/{pads['id']}/restore", json={"version": pads["version"]})
        .status_code
        == 403
    )
    stale = c.post(f"/api/v1/catalog/items/{pads['id']}/restore", json={"version": pads["version"] - 1})
    assert stale.status_code == 409
    back = c.post(f"/api/v1/catalog/items/{pads['id']}/restore", json={"version": pads["version"]})
    assert back.status_code == 200 and back.json()["status"] == "active"
    svc_back = c.post(f"/api/v1/catalog/items/{svc['id']}/restore", json={"version": svc["version"]})
    assert svc_back.status_code == 200 and svc_back.json()["status"] == "active"
    assert (
        c.post(f"/api/v1/catalog/items/{svc['id']}/restore", json={"version": svc_back.json()["version"]}).json()[
            "code"
        ]
        == "catalog.item_not_archived"
    )
    listed = c.get("/api/v1/catalog/items", params={"kind": "service"}).json()["items"]
    assert svc["id"] in [i["id"] for i in listed]
    other = client(business=make_business("example-garden-store"))
    assert other.post(f"/api/v1/catalog/items/{svc['id']}/restore", json={"version": 1}).status_code == 404
