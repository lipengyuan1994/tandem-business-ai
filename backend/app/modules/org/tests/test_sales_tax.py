"""The exact sales tax column participates in the existing versioned settings API."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.platform.db import transaction
from app.platform.testing.auth import ctx_for

from .. import public


def test_sales_tax_settings_round_trip_version_and_public_read(
    client: Any, cycle: uuid.UUID, owner_engine: Engine
) -> None:
    c = client(cycle)
    current = c.get("/api/v1/org/settings").json()
    assert Decimal(current["settings"]["sales_tax_rate"]) == 0
    body = {"version": current["version"], "settings": {**current["settings"], "sales_tax_rate": "0.0825"}}
    changed = c.patch("/api/v1/org/settings", json=body)
    assert changed.status_code == 200, changed.text
    assert Decimal(changed.json()["settings"]["sales_tax_rate"]) == Decimal("0.0825")
    assert changed.json()["version"] == current["version"] + 1
    assert c.patch("/api/v1/org/settings", json=body).status_code == 409
    assert c.get("/api/v1/org/settings").json() == changed.json()
    with transaction(ctx_for(cycle)) as db:
        assert public.get_sales_tax_rate(db, ctx_for(cycle)) == Decimal("0.0825")
    with owner_engine.connect() as conn:
        rate, settings = conn.execute(
            text("select sales_tax_rate, settings from business_settings where business_id=:b"), {"b": cycle}
        ).one()
        assert rate == Decimal("0.0825") and "sales_tax_rate" not in settings
        changes = conn.execute(
            text("select changes from audit_events where business_id=:b and action='org.settings.updated'"),
            {"b": cycle},
        ).scalar_one()
    assert changes == {"sales_tax_rate": {"from": "0", "to": "0.0825"}}


@pytest.mark.parametrize("rate", ["-0.0001", "1.0001", "0.08255", "NaN", None])
def test_invalid_sales_tax_setting_is_rejected(client: Any, cycle: uuid.UUID, rate: str | None) -> None:
    c = client(cycle)
    current = c.get("/api/v1/org/settings").json()
    body = {"version": current["version"], "settings": {**current["settings"], "sales_tax_rate": rate}}
    assert c.patch("/api/v1/org/settings", json=body).status_code == 422
    assert c.get("/api/v1/org/settings").json() == current
