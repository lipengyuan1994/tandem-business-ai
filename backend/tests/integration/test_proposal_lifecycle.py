"""P2-10: full proposal lifecycle through the HTTP API, the worker, the outbox and domain events."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.platform import audit, events, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.ids import new_id
from app.platform.registry import ExecutionMeta, ExecutorSpec, PreviewLine, Registry

pytestmark = pytest.mark.integration


class ReplyPayload(BaseModel):
    text: str


def _run(db: Session, ctx: Context, p: ReplyPayload, meta: ExecutionMeta) -> dict[str, Any]:
    message_id = new_id()
    audit.record(
        db,
        ctx,
        action="integration.reply.recorded",
        target_type="message",
        target_id=message_id,
        summary="Reply recorded",
    )
    outbox_id = notifications.enqueue_message(
        db,
        ctx,
        channel="simulated",
        recipient="ada@example.com",
        template_key="conversation_reply",
        variables={"body": p.text},
        idempotency_key=f"reply:{meta.idempotency_key}",
        related_type="message",
        related_id=message_id,
    )
    return {"message_id": str(message_id), "outbox_id": str(outbox_id)}


SPEC = ExecutorSpec(
    action_type="integration.send_reply",
    payload_model=ReplyPayload,
    approve_permission="conversations.proposal.approve",
    describe=lambda db, ctx, p: [PreviewLine("Reply", None, p.text)],
    validate=lambda db, ctx, p: None,
    run=_run,
)


@pytest.fixture
def setup(registry_builder: Any) -> tuple[Any, Registry, uuid.UUID]:
    reg = registry_builder(lambda r: r.add_executor(SPEC))
    business = new_id()
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business)
    with transaction(ctx) as db:
        org.create_business(
            db,
            ctx,
            name="Example Cycle Works",
            slug="example-cycle-works",
            timezone="America/Denver",
            currency="USD",
            locale="en-US",
        )
    return create_app(reg), reg, business


def test_proposal_lifecycle_end_to_end(setup: Any, owner_engine: Engine) -> None:
    app, reg, business = setup
    creator, approver = login_as(app, business, ["operator"]), login_as(app, business, ["admin"])

    created = creator.post(
        "/api/v1/approvals/proposals",
        json={
            "action_type": "integration.send_reply",
            "title": "Reply to Ada",
            "payload": {"text": "Tuesday 11:45 works!"},
            "evidence": [{"kind": "message", "excerpt": "Could I bring my bike in next Tuesday morning?"}],
        },
    ).json()
    # Notify approvers while the proposal still needs review, before its approval below.
    events.deliver_pending(reg)
    rev1 = created["current_revision"]
    revised = approver.post(
        f"/api/v1/approvals/proposals/{created['id']}/revisions",
        json={"base_revision_id": rev1["id"], "payload": {"text": "Tuesday at 11:45 works — see you then!"}},
    ).json()
    rev2 = revised["current_revision"]

    stale = approver.post(
        f"/api/v1/approvals/proposals/{created['id']}/decisions",
        json={"decision": "approve", "revision_id": rev1["id"], "payload_hash": rev1["payload_hash"]},
    )
    assert stale.status_code == 409
    ok = approver.post(
        f"/api/v1/approvals/proposals/{created['id']}/decisions",
        json={"decision": "approve", "revision_id": rev2["id"], "payload_hash": rev2["payload_hash"]},
    )
    assert ok.json()["status"] == "approved"

    for _ in range(3):  # worker: execution job, then delivery job, then event deliveries
        jobs.run_pending(reg)
        events.deliver_pending(reg)

    detail = approver.get(f"/api/v1/approvals/proposals/{created['id']}").json()
    assert detail["status"] == "succeeded" and detail["attempts"][0]["status"] == "succeeded"
    outbox = approver.get("/api/v1/approvals/proposals", params={"status": "succeeded"}).json()["items"]
    assert [p["id"] for p in outbox] == [created["id"]]

    with owner_engine.connect() as conn:
        actions = (
            conn.execute(
                text("select action from audit_events where proposal_id = :p order by occurred_at"),
                {"p": created["id"]},
            )
            .scalars()
            .all()
        )
        message = conn.execute(text("select status, variables->>'body', proposal_id from outbox_messages")).one()
        event_types = conn.execute(text("select type from domain_events order by occurred_at")).scalars().all()
        undelivered = conn.execute(text("select count(*) from event_deliveries where status <> 'done'")).scalar()
        notified = conn.execute(
            text("select count(*) from in_app_notifications where kind = 'approval_needed'")
        ).scalar()

    assert actions == [
        "approvals.proposal.created",
        "approvals.proposal.revised",
        "approvals.proposal.approved",
        "integration.reply.recorded",
        "approvals.proposal.executed",
    ]
    assert message.status == "delivered" and message[1] == "Tuesday at 11:45 works — see you then!"
    assert str(message.proposal_id) == created["id"]
    assert event_types[0] == "approvals.proposal_created" and "approvals.proposal_closed" in event_types
    assert "notifications.delivery_status_changed" in event_types
    assert undelivered == 0
    assert notified >= 1  # the admin (and any other approver) got an in-app notification
