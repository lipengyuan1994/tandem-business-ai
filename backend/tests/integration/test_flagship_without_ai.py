"""P3-15: the flagship workflow without AI, on the seeded demo data (01-product-and-scope.md#the-flagship-workflow).

A visitor writes in → staff find the earliest slot → hold it → propose "book + reserve part + reply" → a second person
approves → the worker executes → appointment confirmed, part reserved (available −1, on hand unchanged), reply visible
to the visitor, reorder event raised, timeline and audit complete. Then a second request for the same slot conflicts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.platform import clock, events, jobs
from app.platform.ids import fixture_id
from app.platform.registry import Registry
from app.seed import seed

pytestmark = pytest.mark.integration
CYCLE = fixture_id("biz-cycle")


def _pads(owner: Any) -> dict[str, Any]:
    return dict(next(x for x in owner.get("/api/v1/inventory/levels").json() if x["item_name"] == "Brake pads (pair)"))


def test_flagship_flow_without_ai(registry: Registry, owner_engine: Engine) -> None:
    clock.set_clock(lambda: datetime(2026, 11, 5, 17, 30, tzinfo=UTC))
    seed()
    app = create_app(registry)
    casey, morgan = login_as(app, CYCLE, ["operator"]), login_as(app, CYCLE, ["owner"])
    s = morgan.get("/api/v1/org/settings").json()  # ensure two different people (no self-approval)
    assert s["settings"]["allow_self_approval"] is False

    # 1. A visitor writes in.
    visitor = TestClient(app, base_url="https://testserver")
    chat = visitor.post(
        "/api/v1/public/example-cycle-works/chat",
        json={"name": "Ada", "email": "ada@example.com", "message": "My rear brake squeaks. Next Tuesday morning?"},
    ).json()

    # 2. Staff find Ada, the service and the earliest Tuesday-morning slot (deterministic).
    ada = casey.get("/api/v1/crm/customers/match", params={"email": "ada@example.com"}).json()[0]
    assert ada["score"] == 1.0
    brake = casey.get("/api/v1/catalog/items", params={"q": "Brake service"}).json()["items"][0]
    slot = casey.get(
        "/api/v1/scheduling/slots",
        params={"service_id": brake["id"], "earliest": "2026-11-10T16:00:00Z", "latest": "2026-11-10T19:00:00Z"},
    ).json()[0]
    assert slot["local_label"] == "Tue 10 Nov 11:45 MST"

    # 3. Hold it, then propose the composite booking.
    hold = casey.post(
        "/api/v1/scheduling/holds",
        json={
            "service_id": brake["id"],
            "resource_id": slot["resource_id"],
            "customer_id": ada["customer_id"],
            "starts_at": slot["starts_at"],
        },
    ).json()
    assert hold["status"] == "held"
    assert Decimal(_pads(morgan)["available"]) == 2  # holds never reserve stock
    proposal = casey.post(
        "/api/v1/approvals/proposals",
        json={
            "action_type": "scheduling.book_appointment",
            "title": "Book Ada's brake service",
            "payload": {
                "customer": {"id": ada["customer_id"]},
                "service_id": brake["id"],
                "resource_id": slot["resource_id"],
                "starts_at": slot["starts_at"],
                "hold_id": hold["id"],
                "reply": {
                    "conversation_id": chat["conversation_id"],
                    "body": "You're booked for Tue 10 Nov at 11:45. See you!",
                },
            },
            "evidence": [{"kind": "message", "excerpt": "My rear brake squeaks. Next Tuesday morning?"}],
        },
    ).json()

    # 4. A second person approves the exact revision; the worker executes.
    rev = proposal["current_revision"]
    approved = morgan.post(
        f"/api/v1/approvals/proposals/{proposal['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    assert approved.json()["status"] == "approved"
    for _ in range(3):
        jobs.run_pending(registry)
        events.deliver_pending(registry)
    detail = morgan.get(f"/api/v1/approvals/proposals/{proposal['id']}").json()
    assert detail["status"] == "succeeded", detail

    # 5. Outcomes.
    appointment = morgan.get(f"/api/v1/scheduling/appointments/{hold['id']}").json()
    assert appointment["status"] == "confirmed"  # the hold itself was confirmed
    pads = _pads(morgan)
    assert (Decimal(pads["on_hand"]), Decimal(pads["available"])) == (3, 1)
    assert (
        visitor.get("/api/v1/public/example-cycle-works/chat")
        .json()["messages"][-1]["body"]
        .startswith("You're booked")
    )
    with owner_engine.connect() as conn:
        crossed = (
            conn.execute(text("select payload from domain_events where type = 'inventory.stock_below_reorder_level'"))
            .scalars()
            .all()
        )
        delivered = conn.execute(
            text("select status from outbox_messages where proposal_id = :p"), {"p": proposal["id"]}
        ).scalar()
        actions = (
            conn.execute(
                text("select action from audit_events where proposal_id = :p order by occurred_at"),
                {"p": proposal["id"]},
            )
            .scalars()
            .all()
        )
    assert [c["available"] for c in crossed if c["item_id"] == pads["item_id"]] == ["1.000"]  # crossed once, now
    assert delivered == "delivered"
    assert actions[0] == "approvals.proposal.created" and actions[-1] == "approvals.proposal.executed"
    assert "scheduling.appointment.confirmed" in actions and "conversations.reply_sent" in actions
    assert any(
        s["item_name"] == "Brake pads (pair)" for s in morgan.get("/api/v1/inventory/reorder-suggestions").json()
    )
    timeline = morgan.get(f"/api/v1/crm/customers/{ada['customer_id']}/timeline").json()
    assert {t["kind"] for t in timeline} >= {"appointment", "message"}

    # 6. A second request for the same slot gets a clear conflict.
    dana = casey.get("/api/v1/crm/customers/match", params={"name": "Dana Example"}).json()[0]
    clash = casey.post(
        "/api/v1/scheduling/appointments",
        json={
            "customer_id": dana["customer_id"],
            "service_id": brake["id"],
            "resource_id": slot["resource_id"],
            "starts_at": slot["starts_at"],
        },
    )
    assert clash.status_code == 409 and clash.json()["code"] == "scheduling.slot_unavailable"
