"""M09 audit tests."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org import public as org
from app.platform import audit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.ids import new_id
from app.platform.registry import Registry


@pytest.fixture
def app(registry: Registry) -> Any:
    return create_app(registry)


def _business(slug: str) -> uuid.UUID:
    business_id = new_id()
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business_id)
    with transaction(ctx) as db:
        org.create_business(db, ctx, name=slug, slug=slug, timezone="America/Denver", currency="USD", locale="en-US")
    return business_id


def _record(business: uuid.UUID, n: int, action: str = "crm.customer.updated") -> None:
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business)
    with transaction(ctx) as db:
        for i in range(n):
            audit.record(
                db,
                ctx,
                action=action,
                target_type="customer",
                target_id=new_id(),
                summary=f"event {i}",
                changes={"email": {"from": "[redacted]", "to": "[redacted]"}},
            )


def test_audit_filters_and_pagination(app: Any) -> None:
    business = _business("example-cycle-works")
    _record(business, 7)
    _record(business, 2, action="org.settings.updated")
    client = login_as(app, business, ["auditor"])
    first = client.get("/api/v1/audit/events", params={"action_prefix": "crm.", "limit": 5}).json()
    assert len(first["items"]) == 5 and first["next_cursor"]
    second = client.get(
        "/api/v1/audit/events", params={"action_prefix": "crm.", "limit": 5, "cursor": first["next_cursor"]}
    ).json()
    assert len(second["items"]) == 2 and second["next_cursor"] is None
    ids = [e["id"] for e in first["items"] + second["items"]]
    assert len(set(ids)) == 7
    assert first["items"][0]["actor_name"] == "System"
    one = client.get(f"/api/v1/audit/events/{ids[0]}")
    assert one.status_code == 200 and one.json()["id"] == ids[0]


def test_export_is_audited_and_redacted(app: Any) -> None:
    business = _business("example-cycle-works")
    _record(business, 3)
    client = login_as(app, business, ["auditor"])
    response = client.get("/api/v1/audit/export.csv", params={"action_prefix": "crm."})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][0] == "occurred_at" and len(rows) == 4
    assert "[redacted]" in rows[1][-1]
    events = client.get("/api/v1/audit/events", params={"action_prefix": "audit.exported"}).json()["items"]
    assert len(events) == 1


def test_auditor_role_can_read_and_export_but_operator_cannot_export(app: Any) -> None:
    business = _business("example-cycle-works")
    operator = login_as(app, business, ["operator"])
    assert operator.get("/api/v1/audit/events").status_code == 403
    assert operator.get("/api/v1/audit/export.csv").status_code == 403
    admin = login_as(app, business, ["admin"])
    assert admin.get("/api/v1/audit/events").status_code == 200
    assert admin.get("/api/v1/audit/export.csv").status_code == 403  # export is owner/auditor only


def test_cross_business_events_invisible(app: Any) -> None:
    cycle, garden = _business("example-cycle-works"), _business("example-garden-store")
    _record(garden, 2)
    client = login_as(app, cycle, ["auditor"])
    items = client.get("/api/v1/audit/events", params={"action_prefix": "crm."}).json()["items"]
    assert items == []


@pytest.mark.parametrize("value", ["=1+1", "+1+1", "-1+1", "@SUM(1)", "  =1+1", "Ordinary name"])
def test_audit_csv_neutralizes_text_formulas(app: Any, owner_engine: Any, value: str) -> None:
    from sqlalchemy import text

    business = _business("example-cycle-works")
    client = login_as(app, business, ["owner"])
    with owner_engine.begin() as conn:
        user = conn.execute(text("select user_id from memberships where business_id=:b"), {"b": business}).scalar_one()
        conn.execute(text("update users set display_name=:v where id=:i"), {"v": value, "i": user})
    client.post("/api/v1/crm/customers", json={"display_name": "Synthetic Example"})
    rows = list(
        csv.DictReader(
            io.StringIO(client.get("/api/v1/audit/export.csv", params={"action_prefix": "crm.customer.created"}).text)
        )
    )
    assert len(rows) == 1
    assert rows[0]["actor"] == ("'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value)
