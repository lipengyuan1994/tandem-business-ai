from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.inventory import public as inventory
from app.platform.db import transaction
from app.platform.errors import Conflict
from app.platform.registry import ExecutionMeta, Registry
from app.platform.testing.auth import ctx_for

from .. import permissions as P
from .. import service
from .test_purchasing import BASE, action, create


def draft(s: Any) -> dict[str, Any]:
    r = s["client"].post(
        "/api/v1/inventory/reorder-drafts",
        json={"lines": [{"item_id": s["item"], "quantity": "4"}], "note": "Example restock"},
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def test_conversion_executor_binds_prices_and_marks_once(shop: Any, registry: Registry) -> None:
    d = draft(shop)
    spec = registry.executors["purchasing.create_po_from_draft"]
    p = spec.payload_model.model_validate({"draft_id": d["id"], "supplier_id": shop["supplier"]["id"], "version": 1})
    ctx = ctx_for(shop["business"], P.ALL)
    meta = ExecutionMeta(uuid.uuid4(), uuid.uuid4(), "example-conversion")
    with transaction(ctx) as db:
        spec.validate(db, ctx, p)
        assert [(line.label, line.before, line.after) for line in spec.describe(db, ctx, p)] == [
            ("Supplier", None, "Example Parts"),
            ("Example pads", None, "4 × 12.00 USD = 48.00 USD"),
            ("Total", None, "48.00 USD"),
        ]
    s = shop["supplier"]
    changed = shop["client"].patch(
        BASE + "/suppliers/" + s["id"],
        json={"version": s["version"], "items": [{"catalog_item_id": shop["item"], "last_price_minor": 1300}]},
    )
    assert changed.status_code == 200
    with pytest.raises(Conflict, match=r"purchasing\.preview_changed"):
        with transaction(ctx) as db:
            spec.run(db, ctx, p, meta)
    with transaction(ctx) as db:
        spec.validate(db, ctx, p)
        result = spec.run(db, ctx, p, meta)
    po = shop["client"].get(BASE + "/purchase-orders/" + result["purchase_order_id"]).json()
    assert po["status"] == "draft" and po["number"] is None and po["source_draft_id"] == d["id"]
    assert po["lines"][0]["unit_cost_minor"] == 1300
    with transaction(ctx) as db:
        converted = inventory.read_reorder_draft(db, ctx, uuid.UUID(d["id"]))
        assert converted.status == "sent_to_purchasing" and converted.version == 2
    duplicate = shop["client"].post(
        BASE + "/purchase-orders/from-draft", json={"draft_id": d["id"], "supplier_id": s["id"], "version": 1}
    )
    assert duplicate.status_code == 409


def test_approval_executor_rejects_changed_version(shop: Any, registry: Registry) -> None:
    po = create(shop)
    spec = registry.executors["purchasing.approve_po"]
    p = spec.payload_model.model_validate({"purchase_order_id": po["id"], "version": po["version"]})
    ctx = ctx_for(shop["business"], P.ALL)
    with transaction(ctx) as db:
        spec.validate(db, ctx, p)
        assert [(line.label, line.before, line.after) for line in spec.describe(db, ctx, p)] == [
            ("Supplier", None, "Example Parts"),
            ("Status", "draft", "approved"),
            ("Example pads", None, "10 × 12.00 USD = 120.00 USD"),
            ("Total", None, "120.00 USD"),
        ]
    changed = shop["client"].patch(
        BASE + "/purchase-orders/" + po["id"], json={"version": po["version"], "notes": "Changed"}
    )
    assert changed.status_code == 200
    with pytest.raises(Conflict, match=r"purchasing\.version_conflict"):
        with transaction(ctx) as db:
            spec.run(db, ctx, p, ExecutionMeta(uuid.uuid4(), uuid.uuid4(), "example-approve"))
    p.version = changed.json()["version"]
    with transaction(ctx) as db:
        assert (
            spec.run(db, ctx, p, ExecutionMeta(uuid.uuid4(), uuid.uuid4(), "example-approve"))["purchase_order_id"]
            == po["id"]
        )


def test_numbering_concurrency_and_rollback(shop: Any, owner_engine: Engine) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    orders = [create(shop) for _ in range(4)]
    ctx = ctx_for(shop["business"], P.ALL)
    barrier = threading.Barrier(4)

    def approve(po: Any) -> str:
        barrier.wait(timeout=10)
        with transaction(ctx) as db:
            return str(service.transition(db, ctx, uuid.UUID(po["id"]), po["version"], "approve").number)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(approve, orders)) == [f"PO-2026-{i:04d}" for i in range(1, 5)]
    po = create(shop)
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction(ctx) as db:
            service.transition(db, ctx, uuid.UUID(po["id"]), po["version"], "approve")
            raise RuntimeError("rollback")
    assert action(shop, po, "approve")["number"] == "PO-2026-0005"
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from audit_events where action='purchasing.po.approve'")) == 5


def test_concurrent_idempotency_creates_one_order(shop: Any) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2)
    body = {
        "supplier_id": shop["supplier"]["id"],
        "lines": [{"catalog_item_id": shop["item"], "quantity": "2", "unit_cost_minor": 1200}],
    }

    def post(_: int) -> Any:
        barrier.wait(timeout=10)
        r = shop["client"].post(BASE + "/purchase-orders", json=body, headers={"Idempotency-Key": "same-concurrent-po"})
        assert r.status_code == 201, r.text
        return r.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(post, range(2))
    assert first == second
    assert len(shop["client"].get(BASE + "/purchase-orders").json()["items"]) == 1


def test_executor_checks_actual_creation_values_after_snapshot(
    shop: Any, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = draft(shop)
    spec = registry.executors["purchasing.create_po_from_draft"]
    p = spec.payload_model.model_validate({"draft_id": d["id"], "supplier_id": shop["supplier"]["id"], "version": 1})
    ctx = ctx_for(shop["business"], P.ALL)
    with transaction(ctx) as db:
        spec.validate(db, ctx, p)
    currencies = iter(["USD", "EUR"])
    monkeypatch.setattr(service.catalog, "get_default_currency", lambda db, ctx: next(currencies))
    with pytest.raises(Conflict, match=r"purchasing\.preview_changed"):
        with transaction(ctx) as db:
            spec.run(db, ctx, p, ExecutionMeta(uuid.uuid4(), uuid.uuid4(), "example-race"))
    assert shop["client"].get(BASE + "/purchase-orders").json()["items"] == []
    with transaction(ctx) as db:
        assert inventory.read_reorder_draft(db, ctx, uuid.UUID(d["id"])).status == "draft"
