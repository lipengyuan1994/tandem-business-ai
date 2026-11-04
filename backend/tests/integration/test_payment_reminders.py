"""Approved payment reminders across sales, approvals, audit and the outbox."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.approvals.engine import execute
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.modules.sales.tests.factories import BASE, NOW, Shop
from app.modules.sales.tests.test_reminders import invoice
from app.platform import clock
from app.platform.db import transaction
from app.platform.errors import Conflict
from app.platform.registry import ExecutionMeta, Registry
from app.platform.testing.auth import ctx_for


@pytest.fixture
def shop(registry: Registry) -> Shop:
    clock.set_clock(lambda: NOW)
    return Shop(create_app(registry), make_business())


def test_payment_reminder_executor_sends_email_once_and_records_time(
    shop: Shop, registry: Registry, owner_engine: Engine
) -> None:
    row = invoice(shop, "2026-10-01")
    spec = registry.executors["sales.send_payment_reminder"]
    assert spec.approve_permission == "sales.document.write"
    ctx = ctx_for(shop.business, ["sales.document.write"])
    stale = spec.payload_model.model_validate({"invoice_id": row["id"], "invoice_version": 1, "body": "Please pay."})
    with pytest.raises(Conflict, match=r"sales\.version_conflict"), transaction(ctx) as db:
        spec.run(db, ctx, stale, ExecutionMeta(uuid.uuid4(), uuid.uuid4(), "stale"))
    response = shop.owner.post(
        f"{BASE}/invoices/{row['id']}/reminders", json={"body": "Please pay. Thanks!", "version": row["version"]}
    )
    assert response.status_code == 201, response.text
    proposal_id = response.json()["id"]
    detail = shop.owner.get(f"/api/v1/approvals/proposals/{proposal_id}").json()
    assert detail["origin_kind"] == "staff"
    assert detail["current_revision"]["payload"] == {
        "invoice_id": row["id"],
        "invoice_version": row["version"],
        "body": "Please pay. Thanks!",
    }
    # No email or timestamp before approval; use a different staff member to respect self-approval policy.
    assert shop.owner.get(f"{BASE}/invoices/{row['id']}").json()["last_reminder_at"] is None
    approver = login_as(shop.app, shop.business, ["operator"])
    revision = detail["current_revision"]
    decision = approver.post(
        f"/api/v1/approvals/proposals/{proposal_id}/decisions",
        json={"decision": "approve", "revision_id": revision["id"], "payload_hash": revision["payload_hash"]},
    )
    assert decision.status_code == 200, decision.text
    assert execute(shop.business, uuid.UUID(proposal_id), uuid.UUID(revision["id"])) == "succeeded"
    assert execute(shop.business, uuid.UUID(proposal_id), uuid.UUID(revision["id"])) == "done"
    updated = shop.owner.get(f"{BASE}/invoices/{row['id']}").json()
    assert datetime.fromisoformat(updated["last_reminder_at"]) == clock.now()
    assert updated["version"] == row["version"] + 1
    with owner_engine.connect() as conn:
        messages = conn.execute(
            text("select recipient, template_key, variables from outbox_messages where related_id=:id"),
            {"id": row["id"]},
        ).all()
        assert len(messages) == 1
        assert messages[0].recipient == "ada@example.com" and messages[0].template_key == "payment_reminder"
        from app.modules.notifications.templates import render

        subject, body = render(messages[0].template_key, messages[0].variables)
        assert subject == f"Payment reminder: invoice {row['number']}"
        assert "Please pay. Thanks!" in body and "63.00 USD" in body and "2026-10-01" in body
        assert (
            conn.scalar(
                text("select count(*) from audit_events where action='sales.payment_reminder.sent' and target_id=:id"),
                {"id": row["id"]},
            )
            == 1
        )
