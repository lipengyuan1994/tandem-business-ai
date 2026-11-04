from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Engine, text

from .test_purchasing import BASE, action, create, receive


def test_exact_110_percent_is_allowed_and_wrong_line_rolls_back(shop: Any, owner_engine: Engine) -> None:
    po = action(shop, action(shop, create(shop), "approve"), "send")
    invalid = shop["client"].post(
        f"{BASE}/purchase-orders/{po['id']}/receipts",
        json={
            "version": po["version"],
            "lines": [
                {"po_line_id": po["lines"][0]["id"], "quantity": "1"},
                {"po_line_id": str(uuid.uuid4()), "quantity": "1"},
            ],
        },
    )
    assert invalid.status_code == 422
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from goods_receipts")) == 0
        assert db.scalar(text("select count(*) from stock_movements")) == 0
    r = receive(shop, po, "11")
    assert r.status_code == 201 and r.json()["status"] == "received"


def test_stale_receipts_and_duplicate_lines_do_not_change_stock(shop: Any) -> None:
    po = action(shop, action(shop, create(shop), "approve"), "send")
    first = receive(shop, po, "4")
    assert first.status_code == 201
    stale = receive(shop, po, "6")
    assert stale.status_code == 409 and stale.json()["code"] == "purchasing.version_conflict"
    po = first.json()
    invalid = shop["client"].post(
        f"{BASE}/purchase-orders/{po['id']}/receipts",
        json={
            "version": po["version"],
            "lines": [{"po_line_id": po["lines"][0]["id"], "quantity": "4"}] * 2,
        },
    )
    assert invalid.status_code == 422
    assert shop["client"].get("/api/v1/inventory/levels").json()[0]["on_hand"] == "4.000"


def test_two_receipts_race_on_same_version_only_one_wins(shop: Any) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    po = action(shop, action(shop, create(shop), "approve"), "send")
    barrier = threading.Barrier(2)

    def post(_: int) -> int:
        barrier.wait(timeout=10)
        return int(receive(shop, po, "6").status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(post, range(2))) == [201, 409]
    assert shop["client"].get("/api/v1/inventory/levels").json()[0]["on_hand"] == "6.000"
