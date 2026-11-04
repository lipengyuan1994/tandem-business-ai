from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform.db import transaction
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for

BASE = "/api/v1/purchasing"


def create(s: Any, quantity: str = "10") -> dict[str, Any]:
    r = s["client"].post(
        BASE + "/purchase-orders",
        json={
            "supplier_id": s["supplier"]["id"],
            "lines": [
                {"catalog_item_id": s["item"], "quantity": quantity, "unit_cost_minor": 1200},
            ],
        },
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def action(s: Any, po: Any, name: str) -> Any:
    r = s["client"].post(f"{BASE}/purchase-orders/{po['id']}/{name}", json={"version": po["version"]})
    assert r.status_code == 200, r.text
    return r.json()


def receive(s: Any, po: Any, quantity: str, **extra: Any) -> Any:
    return s["client"].post(
        f"{BASE}/purchase-orders/{po['id']}/receipts",
        json={
            "version": po["version"],
            "lines": [{"po_line_id": po["lines"][0]["id"], "quantity": quantity}],
            **extra,
        },
    )


def test_receipt_lifecycle_and_costed_movements(shop: Any, owner_engine: Engine) -> None:
    po = create(shop)
    assert po["number"] is None and po["total_minor"] == 12000
    assert shop["client"].post(f"{BASE}/purchase-orders/{po['id']}/send", json={"version": 1}).status_code == 409
    po = action(shop, po, "approve")
    assert po["number"] == "PO-2026-0001"
    po = action(shop, po, "send")
    r = receive(shop, po, "4")
    assert r.status_code == 201, r.text
    po = r.json()
    assert po["status"] == "partially_received"
    r = receive(shop, po, "6")
    assert r.status_code == 201, r.text
    po = r.json()
    assert po["status"] == "received" and len(po["receipts"]) == 2
    level = shop["client"].get("/api/v1/inventory/levels").json()[0]
    assert Decimal(level["on_hand"]) == 10
    with owner_engine.connect() as db:
        rows = db.execute(
            text(
                "select quantity, unit_cost_minor, ref_id from stock_movements where ref_type='goods_receipt' "
                "order by quantity"
            )
        ).all()
        assert [(r.quantity, r.unit_cost_minor) for r in rows] == [(4, 1200), (6, 1200)]
        assert {str(r.ref_id) for r in rows} == {r["id"] for r in po["receipts"]}


def test_over_receipt_cumulative_guard_and_override(shop: Any) -> None:
    po = action(shop, action(shop, create(shop), "approve"), "send")
    po = receive(shop, po, "5").json()
    denied = receive(shop, po, "7")
    assert denied.status_code == 409 and denied.json()["code"] == "purchasing.over_receipt"
    assert receive(shop, po, "7", override=True).status_code == 422
    r = receive(shop, po, "7", override=True, reason="Extra sealed pack accepted")
    assert r.status_code == 201 and r.json()["status"] == "received"
    assert r.json()["receipts"][-1]["override_reason"] == "Extra sealed pack accepted"


@pytest.mark.parametrize(
    "role,write,approve,receive_ok",
    [
        ("owner", True, True, True),
        ("admin", True, True, True),
        ("inventory_manager", True, False, True),
        ("operator", False, False, True),
        ("auditor", False, False, False),
    ],
)
def test_role_permissions(shop: Any, role: str, write: bool, approve: bool, receive_ok: bool) -> None:
    po = create(shop)
    c = login_as(shop["app"], shop["business"], [role])
    assert c.get(BASE + "/suppliers").status_code == 200
    r = c.patch(BASE + "/suppliers/" + shop["supplier"]["id"], json={"version": 1, "notes": "Example note"})
    assert r.status_code == (200 if write else 403)
    r = c.post(f"{BASE}/purchase-orders/{po['id']}/approve", json={"version": po["version"]})
    assert r.status_code == (200 if approve else 403)
    if approve:
        po = r.json()
    else:
        po = action(shop, po, "approve")
    po = action(shop, po, "send")
    shop["client"] = c
    assert receive(shop, po, "1").status_code == (201 if receive_ok else 403)
    if receive_ok and not approve:
        po = c.get(f"{BASE}/purchase-orders/{po['id']}").json()
        assert receive(shop, po, "11", override=True, reason="Extra").status_code == 403


def test_idempotency_versions_immutability_and_cancel(shop: Any) -> None:
    body = {
        "supplier_id": shop["supplier"]["id"],
        "lines": [{"catalog_item_id": shop["item"], "quantity": "10", "unit_cost_minor": 123}],
    }
    c = shop["client"]
    headers = {"Idempotency-Key": "purchasing-create"}
    first = c.post(BASE + "/purchase-orders", json=body, headers=headers)
    assert first.status_code == 201
    assert c.post(BASE + "/purchase-orders", json=body, headers=headers).json() == first.json()
    po = first.json()
    edit = c.patch(f"{BASE}/purchase-orders/{po['id']}", json={"version": 1, "notes": "Example"})
    assert edit.status_code == 200
    assert c.post(f"{BASE}/purchase-orders/{po['id']}/approve", json={"version": 1}).status_code == 409
    po = action(shop, edit.json(), "approve")
    assert (
        c.patch(f"{BASE}/purchase-orders/{po['id']}", json={"version": po["version"], "notes": "Changed"}).status_code
        == 409
    )
    po = action(shop, po, "send")
    rb = {"version": po["version"], "lines": [{"po_line_id": po["lines"][0]["id"], "quantity": "2"}]}
    rh = {"Idempotency-Key": "purchasing-receipt"}
    r = c.post(f"{BASE}/purchase-orders/{po['id']}/receipts", json=rb, headers=rh)
    assert r.status_code == 201
    assert c.post(f"{BASE}/purchase-orders/{po['id']}/receipts", json=rb, headers=rh).json() == r.json()
    po = action(shop, r.json(), "cancel")
    assert po["status"] == "cancelled"
    assert receive(shop, po, "1").status_code == 409


def test_suggestions_open_quantities_and_tenant_export(shop: Any, registry: Registry) -> None:
    c = shop["client"]
    assert (
        c.put(
            "/api/v1/inventory/reorder-rules/" + shop["item"],
            json={"reorder_point": "5", "target_level": "20"},
        ).status_code
        == 200
    )
    po = create(shop)
    suggestions = c.get(BASE + "/reorder-suggestions").json()
    assert Decimal(suggestions[0]["suggested"]) == 20  # unapproved drafts are not committed supply
    po = action(shop, po, "approve")
    suggestions = c.get(BASE + "/reorder-suggestions").json()
    assert Decimal(suggestions[0]["open_po_quantity"]) == 10 and Decimal(suggestions[0]["suggested"]) == 10
    other = make_business(slug="other-example")
    other_client = login_as(shop["app"], other, ["owner"])
    assert other_client.get(f"{BASE}/purchase-orders/{po['id']}").status_code == 404
    with transaction(ctx_for(other)) as db:
        assert db.scalar(text("select count(*) from suppliers")) == 0
    with transaction(ctx_for(shop["business"])) as db:
        exported = registry.export_handlers["purchasing"](db, ctx_for(shop["business"]))
    assert set(exported) == {
        "suppliers",
        "supplier_items",
        "purchase_orders",
        "purchase_order_lines",
        "goods_receipts",
        "goods_receipt_lines",
        "purchase_order_sequences",
        "quote_requests",
        "quote_request_lines",
        "quote_request_suppliers",
        "supplier_quotes",
        "supplier_quote_lines",
    }
    assert "purchasing" in registry.erasure_handlers


def test_purchase_order_date_and_numbering_follow_business_midnight(shop: Any, owner_engine: Engine) -> None:
    from datetime import UTC, datetime

    from app.platform import clock

    with owner_engine.begin() as conn:
        conn.execute(text("update businesses set timezone='America/New_York' where id=:b"), {"b": shop["business"]})
    clock.set_clock(lambda: datetime(2027, 1, 1, 1, tzinfo=UTC))
    shop["client"] = login_as(shop["app"], shop["business"], ["owner"])
    po = action(shop, create(shop), "approve")
    assert po["approved_date"] == "2026-12-31" and po["number"].startswith("PO-2026-")
