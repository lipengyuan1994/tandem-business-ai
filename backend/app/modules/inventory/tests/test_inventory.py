"""M07 inventory tests (docs/roadmap/modules/M07-inventory.md#tests-must-exist)."""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.inventory import public as inventory
from app.modules.org.testing import make_business, system_ctx
from app.platform import jobs
from app.platform.db import transaction
from app.platform.errors import Conflict, Invalid
from app.platform.ids import new_id
from app.platform.registry import Registry

PERMS = frozenset({"inventory.stock.read", "inventory.stock.adjust", "catalog.item.read"})


@pytest.fixture
def biz() -> uuid.UUID:
    return make_business()


@pytest.fixture
def client(registry: Registry, biz: uuid.UUID) -> Any:
    app = create_app(registry)
    return lambda roles=("owner",): login_as(app, biz, list(roles))


@pytest.fixture
def pads(client: Any) -> str:
    c = client()
    item = c.post(
        "/api/v1/catalog/items",
        json={"kind": "product", "sku": "BP-01", "name": "Brake pads (pair)", "is_stock_tracked": True},
    ).json()
    assert (
        c.post(
            "/api/v1/inventory/movements", json={"item_id": item["id"], "quantity": "3", "reason": "receipt"}
        ).status_code
        == 201
    )
    return str(item["id"])


def _level(client: Any, item_id: str) -> dict[str, Any]:
    return next(x for x in client().get("/api/v1/inventory/levels").json() if x["item_id"] == item_id)


def _reserve(biz: uuid.UUID, item_id: str, qty: str, ref: uuid.UUID | None = None) -> uuid.UUID:
    ctx = system_ctx(biz, PERMS)
    ref = ref or new_id()
    with transaction(ctx) as db:
        inventory.reserve_stock(
            db, ctx, item_id=uuid.UUID(item_id), quantity=Decimal(qty), ref_type="appointment", ref_id=ref
        )
    return ref


def test_reserve_reduces_available_not_on_hand(client: Any, biz: uuid.UUID, pads: str) -> None:
    _reserve(biz, pads, "1")
    level = _level(client, pads)
    assert (Decimal(level["on_hand"]), Decimal(level["reserved"]), Decimal(level["available"])) == (3, 1, 2)


def test_reserve_rejects_when_available_insufficient_unless_backorders(client: Any, biz: uuid.UUID, pads: str) -> None:
    with pytest.raises(Conflict, match=r"inventory\.insufficient_stock"):
        _reserve(biz, pads, "4")
    owner = client()
    s = owner.get("/api/v1/org/settings").json()
    owner.patch(
        "/api/v1/org/settings", json={"version": s["version"], "settings": {**s["settings"], "allow_backorders": True}}
    )
    _reserve(biz, pads, "4")
    assert Decimal(_level(client, pads)["available"]) == -1


@pytest.mark.concurrency
def test_concurrent_reservations_cannot_oversell(client: Any, biz: uuid.UUID, pads: str) -> None:
    results: list[str] = []
    barrier = threading.Barrier(6)

    def grab() -> None:
        barrier.wait()
        try:
            _reserve(biz, pads, "1")
            results.append("ok")
        except Conflict:
            results.append("conflict")

    threads = [threading.Thread(target=grab) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 3 and results.count("conflict") == 3
    assert Decimal(_level(client, pads)["available"]) == 0


def test_release_is_idempotent_and_creates_no_movement(
    client: Any, biz: uuid.UUID, pads: str, owner_engine: Engine
) -> None:
    ref = _reserve(biz, pads, "2")
    ctx = system_ctx(biz, PERMS)
    with transaction(ctx) as db:
        assert inventory.release_reservations(db, ctx, ref_type="appointment", ref_id=ref) == 1
        assert inventory.release_reservations(db, ctx, ref_type="appointment", ref_id=ref) == 0
    level = _level(client, pads)
    assert Decimal(level["reserved"]) == 0 and Decimal(level["on_hand"]) == 3
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from stock_movements")).scalar() == 1  # only the receipt


def test_consume_creates_consumption_movement(client: Any, biz: uuid.UUID, pads: str) -> None:
    ref = _reserve(biz, pads, "1")
    ctx = system_ctx(biz, PERMS)
    with transaction(ctx) as db:
        inventory.consume_reservations(db, ctx, ref_type="appointment", ref_id=ref)
    level = _level(client, pads)
    assert (Decimal(level["on_hand"]), Decimal(level["reserved"])) == (2, 0)
    ledger = client().get(f"/api/v1/inventory/items/{pads}").json()["movements"]
    assert [(m["reason"], Decimal(m["quantity"])) for m in ledger] == [("consumption", -1), ("receipt", 3)]


def test_count_correction_writes_difference_movement(client: Any, pads: str) -> None:
    c = client(["inventory_manager"])
    m = c.post("/api/v1/inventory/counts", json={"item_id": pads, "counted": "1", "note": "shelf count"}).json()
    assert m["reason"] == "count_correction" and Decimal(m["quantity"]) == -2
    assert c.post("/api/v1/inventory/counts", json={"item_id": pads, "counted": "1"}).json() is None


def test_movements_are_append_only_for_app_role(biz: uuid.UUID, pads: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"), transaction(system_ctx(biz)) as db:
        db.execute(text("update stock_movements set quantity = 100"))


def test_invalid_quantities_and_overdraw(client: Any, biz: uuid.UUID, pads: str) -> None:
    c = client()
    frac = c.post("/api/v1/inventory/movements", json={"item_id": pads, "quantity": "0.5", "reason": "adjustment_gain"})
    assert frac.status_code == 422 and frac.json()["code"] == "inventory.fractional_quantity"
    over = c.post("/api/v1/inventory/movements", json={"item_id": pads, "quantity": "-5", "reason": "adjustment_loss"})
    assert over.status_code == 409 and over.json()["code"] == "inventory.insufficient_on_hand"
    assert (
        c.post(
            "/api/v1/inventory/movements", json={"item_id": pads, "quantity": "-1", "reason": "consumption"}
        ).status_code
        == 422
    )
    assert (
        client(["operator"])
        .post("/api/v1/inventory/movements", json={"item_id": pads, "quantity": "1", "reason": "receipt"})
        .status_code
        == 403
    )
    with pytest.raises(Invalid):
        _reserve(biz, pads, "0")


def test_below_reorder_event_published_once_per_crossing(
    client: Any, biz: uuid.UUID, pads: str, owner_engine: Engine
) -> None:
    client().put(f"/api/v1/inventory/reorder-rules/{pads}", json={"reorder_point": "2", "target_level": "8"})
    _reserve(biz, pads, "1")  # available 3 -> 2: not below
    _reserve(biz, pads, "1")  # 2 -> 1: crosses
    _reserve(biz, pads, "1")  # 1 -> 0: already below, no new event
    with owner_engine.connect() as conn:
        events_ = (
            conn.execute(
                text(
                    "select payload->>'available' from domain_events where type = 'inventory.stock_below_reorder_level'"
                )
            )
            .scalars()
            .all()
        )
    assert events_ == ["1.000"]


def test_reorder_suggestion_formula(client: Any, biz: uuid.UUID, pads: str) -> None:
    c = client()
    c.put(f"/api/v1/inventory/reorder-rules/{pads}", json={"reorder_point": "2", "target_level": "8"})
    _reserve(biz, pads, "2")  # available 1
    s = c.get("/api/v1/inventory/reorder-suggestions").json()
    assert len(s) == 1 and Decimal(s[0]["suggested"]) == 7
    assert s[0]["explanation"] == (
        "Available 1 is below the reorder point 2; ordering 7 brings it to the target level 8."
    )
    assert (
        c.put(f"/api/v1/inventory/reorder-rules/{pads}", json={"reorder_point": "5", "target_level": "5"}).status_code
        == 422
    )


def test_adjust_stock_executor_through_approval(client: Any, pads: str, registry: Registry) -> None:
    operator, manager = client(["operator"]), client(["inventory_manager"])
    p = operator.post(
        "/api/v1/approvals/proposals",
        json={
            "action_type": "inventory.adjust_stock",
            "title": "Shelf count: pads are 2",
            "payload": {"item_id": pads, "quantity": "-1", "reason": "count_correction", "note": "counted 2"},
        },
    ).json()
    assert p["current_revision"]["preview"][0]["before"] == "3"
    rev = p["current_revision"]
    manager.post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    jobs.run_pending(registry)
    assert manager.get(f"/api/v1/approvals/proposals/{p['id']}").json()["status"] == "succeeded"
    assert Decimal(_level(client, pads)["on_hand"]) == 2


@pytest.mark.parametrize("reason", ["consumption", "sale"])
def test_consuming_reserved_stock_does_not_repeat_reorder_crossing(
    client: Any, biz: uuid.UUID, pads: str, owner_engine: Engine, reason: str
) -> None:
    from app.modules.inventory import permissions, service
    from app.modules.inventory.schemas import ReorderRuleIn

    ctx = system_ctx(biz, PERMS | {permissions.REORDER_WRITE})
    with transaction(ctx) as db:
        service.put_rule(db, ctx, uuid.UUID(pads), ReorderRuleIn(reorder_point=Decimal(2), target_level=Decimal(6)))
    ref = _reserve(biz, pads, "3")
    with owner_engine.connect() as conn:
        before = conn.execute(
            text("select count(*) from domain_events where type='inventory.stock_below_reorder_level'")
        ).scalar()
    with transaction(ctx) as db:
        service.consume(db, ctx, ref_type="appointment", ref_id=ref, reason=reason)
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from domain_events where type='inventory.stock_below_reorder_level'")
            ).scalar()
            == before
            == 1
        )
    assert Decimal(_level(client, pads)["available"]) == 0


@pytest.mark.parametrize("operation", ["release", "consume"])
def test_reservation_transitions_lock_item_then_location(
    client: Any, biz: uuid.UUID, pads: str, monkeypatch: Any, operation: str
) -> None:
    from app.modules.inventory import service
    from app.modules.inventory.models import StockLocation
    from app.modules.inventory.schemas import MovementIn

    other = (
        client()
        .post(
            "/api/v1/catalog/items", json={"name": "Another example part", "kind": "product", "is_stock_tracked": True}
        )
        .json()["id"]
    )
    ctx = system_ctx(biz, PERMS)
    ref = new_id()
    with transaction(ctx) as db:
        location1 = service.default_location(db, ctx).id
        location2 = new_id()
        db.add(StockLocation(id=location2, business_id=biz, name="Secondary", is_default=False))
        db.flush()
        keys = [(uuid.UUID(i), loc) for i in (pads, other) for loc in (location1, location2)]
        for item_id, location_id in sorted(keys, reverse=True):
            service.record_movement(
                db,
                ctx,
                MovementIn(item_id=item_id, stock_location_id=location_id, quantity=Decimal(3), reason="receipt"),
            )
            service.reserve(
                db,
                ctx,
                item_id=item_id,
                stock_location_id=location_id,
                quantity=Decimal(1),
                ref_type="appointment",
                ref_id=ref,
            )
    locked: list[tuple[uuid.UUID, uuid.UUID]] = []
    original = service._locked_level

    def capture(db: Any, ctx: Any, item_id: uuid.UUID, location_id: uuid.UUID) -> Any:
        locked.append((item_id, location_id))
        return original(db, ctx, item_id, location_id)

    monkeypatch.setattr(service, "_locked_level", capture)
    with transaction(ctx) as db:
        getattr(service, operation)(db, ctx, ref_type="appointment", ref_id=ref)
    assert set(locked) == set(keys) and locked == sorted(locked)
