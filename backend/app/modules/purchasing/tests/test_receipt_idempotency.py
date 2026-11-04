from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text

from .test_purchasing import BASE, action, create


def test_receipt_replay_returns_original_response_and_records_stock_once(shop: Any, owner_engine: Engine) -> None:
    po = action(shop, action(shop, create(shop), "approve"), "send")
    path = f"{BASE}/purchase-orders/{po['id']}/receipts"
    body = {"version": po["version"], "lines": [{"po_line_id": po["lines"][0]["id"], "quantity": "4"}]}
    headers = {"Idempotency-Key": "receipt-response-lost-retry"}
    first = shop["client"].post(path, json=body, headers=headers)
    assert first.status_code == 201, first.text
    # Reuse the original version and quantities after commit, just as a lost-response retry must do.
    replay = shop["client"].post(path, json=body, headers=headers)
    assert replay.status_code == 201 and replay.json() == first.json()
    assert replay.json()["status"] == "partially_received"
    assert Decimal(replay.json()["lines"][0]["received_quantity"]) == 4
    level = shop["client"].get("/api/v1/inventory/levels").json()[0]
    assert Decimal(level["on_hand"]) == 4
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from goods_receipts")) == 1
        assert db.scalar(text("select count(*) from goods_receipt_lines")) == 1
        assert db.scalar(text("select count(*) from stock_movements where ref_type='goods_receipt'")) == 1
        assert db.scalar(text("select sum(quantity) from stock_movements where ref_type='goods_receipt'")) == 4
    changed = {**body, "lines": [{"po_line_id": po["lines"][0]["id"], "quantity": "5"}]}
    reused = shop["client"].post(path, json=changed, headers=headers)
    assert reused.status_code == 409 and reused.json()["code"] == "platform.idempotency_key_reused"
