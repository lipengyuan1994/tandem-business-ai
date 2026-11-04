from __future__ import annotations

from typing import Any

import pytest

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.registry import Registry

from .test_purchasing import BASE


@pytest.fixture
def shop(registry: Registry) -> Any:
    from datetime import UTC, datetime

    clock.set_clock(lambda: datetime(2026, 10, 28, 12, tzinfo=UTC))
    business = make_business()
    app = create_app(registry)
    client = login_as(app, business, ["owner"])
    item = client.post(
        "/api/v1/catalog/items",
        json={
            "name": "Example pads",
            "kind": "product",
            "is_stock_tracked": True,
        },
    ).json()["id"]
    r = client.post(
        BASE + "/suppliers",
        json={
            "name": "Example Parts",
            "email": "parts@example.com",
            "items": [{"catalog_item_id": item, "supplier_sku": "BP", "last_price_minor": 1200, "lead_time_days": 3}],
        },
    )
    assert r.status_code == 201, r.text
    return dict(app=app, business=business, client=client, item=item, supplier=r.json())
