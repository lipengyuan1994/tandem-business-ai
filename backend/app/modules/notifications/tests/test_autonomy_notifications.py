"""Delayed proposal-created delivery follows the current approval state."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import create_app
from app.modules.approvals import public as approvals
from app.modules.approvals.public import RulePut
from app.modules.identity import public as identity
from app.modules.identity.testing import add_member, login_as, make_user
from app.modules.org.testing import make_business
from app.platform import events, jobs
from app.platform.context import Context, assistant_context
from app.platform.db import transaction
from app.platform.registry import Registry

ACTION = "inventory.create_reorder_draft"


@pytest.mark.parametrize("edit_after", [False, True])
@pytest.mark.parametrize(
    "level,shadow,execute,kind",
    [
        ("act_notify", False, False, "ai_acted"),
        ("act_notify", False, True, "ai_acted"),
        ("act", False, False, None),
        ("act", False, True, None),
        ("act_notify", True, False, "approval_needed"),
        ("ask", True, False, "approval_needed"),
    ],
)
def test_rule_notification(
    registry: Registry, level: str, shadow: bool, execute: bool, kind: str | None, edit_after: bool
) -> None:
    business = make_business()
    owner = make_user()
    add_member(owner, business, ["owner"])
    ctx = Context("notification-test", "user", owner, business, identity.ROLES["owner"])
    client = login_as(create_app(registry), business, ["owner"], user_id=owner)
    operator = login_as(create_app(registry), business, ["operator"])
    response = client.post(
        "/api/v1/catalog/items",
        json={"kind": "product", "name": "Example cable", "unit": "each", "price_minor": 100, "is_stock_tracked": True},
    )
    assert response.status_code == 201
    item = response.json()["id"]
    assert (
        client.put(
            f"/api/v1/inventory/reorder-rules/{item}", json={"reorder_point": "2", "target_level": "5"}
        ).status_code
        == 200
    )
    with transaction(ctx) as db:
        body = RulePut(action_type=ACTION, level=level, version=0)  # type: ignore[arg-type]
        approvals.put_autonomy_rule(db, ctx, body)
        approvals.put_autonomy_rule(db, ctx, body.model_copy(update={"version": 1, "shadow": shadow}))
    ai = assistant_context(
        business,
        frozenset({"ai.assistant.use", "inventory.stock.read"}),
        on_behalf_of_user_id=None,
        ai_run_id=uuid.uuid4(),
    )
    with transaction(ai) as db:
        proposal = approvals.create_proposal(
            db,
            ai,
            action_type=ACTION,
            title="Reorder example cable",
            payload={"lines": [{"item_id": item, "quantity": "5"}]},
        )
    if execute:
        jobs.run_pending(registry)
    if edit_after:
        with transaction(ctx) as db:
            approvals.put_autonomy_rule(
                db,
                ctx,
                body.model_copy(update={"version": 2, "level": "act" if level == "act_notify" else "act_notify"}),
            )
    events.deliver_pending(registry)
    events.deliver_pending(registry)
    notifications = client.get("/api/v1/notifications").json()["items"]
    assert operator.get("/api/v1/notifications").json()["items"] == []
    if kind is None:
        assert notifications == []
    else:
        assert len(notifications) == 1
        note = notifications[0]
        assert note["kind"] == kind and note["link"] == f"/approvals/{proposal.id}"
        if kind == "ai_acted":
            assert note["title"] == "AI acted: Reorder example cable"
            assert "autonomy rule" in note["body"].lower() and "v2" in note["body"]
        elif level == "act_notify":
            assert "shadow mode" in note["body"]


@pytest.mark.parametrize("status", ["approved", "rejected", "cancelled", "expired", "failed", "succeeded"])
def test_non_pending_human_proposals_do_not_request_approval(app_client: Any, status: str, registry: Registry) -> None:
    creator, admin = app_client(["operator"]), app_client(["admin"])
    response = creator.post(
        "/api/v1/approvals/proposals",
        json={"action_type": "testing.noop_write", "title": "Example", "payload": {"name": "x"}},
    )
    assert response.status_code == 201
    proposal = response.json()
    if status in {"approved", "rejected"}:
        rev = proposal["current_revision"]
        assert (
            admin.post(
                f"/api/v1/approvals/proposals/{proposal['id']}/decisions",
                json={
                    "decision": "approve" if status == "approved" else "reject",
                    "revision_id": rev["id"],
                    "payload_hash": rev["payload_hash"],
                },
            ).status_code
            == 200
        )
    else:
        # Exercise delayed delivery for every terminal state without unrelated lifecycle jobs.
        from sqlalchemy import text

        from app.platform.context import system_context

        with transaction(system_context(uuid.UUID(creator.get("/api/v1/auth/me").json()["active_business_id"]))) as db:
            db.execute(text("update action_proposals set status=:s where id=:p"), {"s": status, "p": proposal["id"]})
    events.deliver_pending(registry)
    assert admin.get("/api/v1/notifications").json()["items"] == []


@pytest.fixture
def app_client(registry: Registry) -> Any:
    business = make_business()
    app = create_app(registry)
    return lambda roles: login_as(app, business, roles)
