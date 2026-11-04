"""P1-06: audit writer."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.platform import audit
from app.platform.db import transaction
from app.platform.ids import new_id
from app.platform.testing.auth import ctx_for


def _count(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("select count(*) from audit_events")).scalar_one())


def test_audit_rolls_back_with_business_change(business: Any, owner_engine: Engine) -> None:
    ctx = ctx_for(business)
    with pytest.raises(RuntimeError):
        with transaction(ctx) as db:
            db.execute(
                text("insert into test_items (id, business_id, name) values (:i, :b, 'x')"),
                {"i": new_id(), "b": business},
            )
            audit.record(
                db, ctx, action="test.item.created", target_type="test_item", target_id=None, summary="created"
            )
            raise RuntimeError("business rule failed after writing")
    assert _count(owner_engine) == 0
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from test_items")).scalar() == 0


def test_audit_fills_actor_from_context(business: Any, owner_engine: Engine) -> None:
    ctx = ctx_for(business, user_id=new_id())
    with transaction(ctx) as db:
        audit.record(
            db,
            ctx,
            action="test.item.created",
            target_type="test_item",
            target_id=new_id(),
            summary="s",
            changes={"amount": __import__("decimal").Decimal("1.50")},
        )
    with owner_engine.connect() as conn:
        row = conn.execute(text("select actor_kind, actor_id, request_id, changes from audit_events")).one()
    assert row.actor_kind == "user" and row.actor_id == ctx.user_id and row.request_id == ctx.request_id
    assert row.changes == {"amount": "1.5"}


def test_audit_diff_redacts_fields() -> None:
    before = {"name": "Ada", "email": "ada@example.com", "tier": "a"}
    after = {"name": "Ada E.", "email": "ada2@example.com", "tier": "a"}
    assert audit.diff(before, after, ["name", "email", "tier"], redact=["email"]) == {
        "name": {"from": "Ada", "to": "Ada E."},
        "email": {"from": "[redacted]", "to": "[redacted]"},
    }
