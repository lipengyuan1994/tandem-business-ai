"""P8-23 review: shared erasure checks, persistent blocks and cache invalidation."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from app.modules.admin_ops.tests.test_data import CYCLE, NOW, customer
from app.modules.admin_ops.tests.test_data import demo as demo
from app.modules.identity.testing import login_as
from app.modules.notifications import adapters
from app.modules.notifications import service as notifications
from app.platform.config import get_settings

from .test_regulated import regulated as regulated


def _restrict(owner: Any, person: dict[str, Any]) -> Any:
    return owner.post(
        "/api/v1/admin/erasures", json={"customer_id": person["id"], "confirm_name": person["display_name"]}
    )


@pytest.mark.parametrize("reference", ["customer", "conversation", "appointment"])
def test_restriction_runs_shared_checks_for_open_proposals(
    demo: Any, regulated: None, owner_engine: Engine, reference: str
) -> None:
    owner = demo["owner"]
    person = customer(owner, "Ben Example" if reference == "appointment" else "Ada Example")
    if reference == "customer":
        action = "crm.update_customer"
        payload = {
            "customer_id": person["id"],
            "customer_version": person["version"],
            "changes": {"given_name": "Example"},
        }
    elif reference == "conversation":
        conv = owner.post(
            "/api/v1/conversations",
            json={"channel": "phone_log", "customer_id": person["id"], "body": "Keep this record"},
        ).json()
        action, payload = "conversations.send_reply", {"conversation_id": conv["id"], "body": "Example reply"}
    else:
        with owner_engine.connect() as conn:
            appointment = conn.execute(
                text("select id, version from appointments where customer_id=:c limit 1"), {"c": person["id"]}
            ).one()
        cancelled = owner.post(
            f"/api/v1/scheduling/appointments/{appointment.id}/cancel",
            json={"version": appointment.version, "reason": "Example cancellation"},
        )
        assert cancelled.status_code == 200
        action, payload = (
            "scheduling.cancel_appointment",
            {
                "appointment_id": str(appointment.id),
                "appointment_version": cancelled.json()["version"],
                "reason": "Example cancellation",
            },
        )
    operator = login_as(demo["app"], CYCLE, ["operator"])
    proposal = operator.post(
        "/api/v1/approvals/proposals", json={"action_type": action, "title": "Example proposal", "payload": payload}
    )
    assert proposal.status_code == 201, proposal.text
    blocked = _restrict(owner, person)
    assert blocked.status_code == 409 and blocked.json()["code"] == "admin_ops.erasure_blocked", blocked.text
    assert any("open approval request" in reason for reason in blocked.json()["reasons"])
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("select status from customers where id=:c"), {"c": person["id"]}).scalar_one() == "active"
        )
        assert conn.execute(text("select count(*) from erasure_requests")).scalar_one() == 0
    revision = proposal.json()["current_revision"]
    rejected = owner.post(
        f"/api/v1/approvals/proposals/{proposal.json()['id']}/decisions",
        json={"decision": "reject", "revision_id": revision["id"], "payload_hash": revision["payload_hash"]},
    )
    assert rejected.status_code == 200, rejected.text
    assert _restrict(owner, person).json()["status"] == "restricted"


@pytest.mark.parametrize("status", ["held", "confirmed"])
def test_restriction_blocks_upcoming_appointments_with_same_reasons(
    demo: Any, regulated: None, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    owner = demo["owner"]
    ben = customer(owner, "Ben Example")
    with owner_engine.begin() as conn:
        conn.execute(
            text("update appointments set status=:s, hold_expires_at=:expiry where customer_id=:c"),
            {"s": status, "expiry": NOW + timedelta(minutes=30) if status == "held" else None, "c": ben["id"]},
        )
    restriction = _restrict(owner, ben)
    monkeypatch.setattr(get_settings(), "data_profile", "standard")
    erasure = _restrict(owner, ben)
    assert restriction.status_code == erasure.status_code == 409
    assert restriction.json()["code"] == erasure.json()["code"] == "admin_ops.erasure_blocked"
    assert restriction.json()["reasons"] == erasure.json()["reasons"]
    assert any("upcoming appointment" in reason for reason in restriction.json()["reasons"])


@pytest.mark.parametrize(
    "result,expected",
    [
        (adapters.RetryableError("503"), "blocked"),
        (adapters.Delivered("sent"), "delivered"),
        (adapters.Unknown("timeout"), "unknown"),
        (adapters.PermanentError("rejected"), "failed"),
    ],
)
def test_inflight_outcome_cannot_release_delivery_block(
    demo: Any,
    regulated: None,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    result: adapters.Result,
    expected: str,
) -> None:
    owner = demo["owner"]
    ada = customer(owner, "Ada Example")
    conv = owner.post(
        "/api/v1/conversations", json={"channel": "phone_log", "customer_id": ada["id"], "body": "Keep history"}
    ).json()
    message = owner.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"direction": "outbound", "body": "Already queued"}
    ).json()
    with owner_engine.connect() as conn:
        outbox_id = conn.execute(
            text("select outbox_message_id from messages where id=:m"), {"m": message["id"]}
        ).scalar_one()
    started, finish = threading.Event(), threading.Event()
    calls: list[str] = []

    class InFlight:
        def send(self, rendered: Any, key: str) -> adapters.Result:
            calls.append(key)
            started.set()
            assert finish.wait(10)
            return result

    monkeypatch.setitem(adapters.ADAPTERS, "simulated", InFlight())
    with ThreadPoolExecutor(max_workers=1) as pool:
        attempt = pool.submit(notifications.deliver, CYCLE, outbox_id)
        try:
            assert started.wait(10)
            with owner_engine.connect() as conn:
                assert (
                    conn.execute(text("select status from outbox_messages where id=:i"), {"i": outbox_id}).scalar_one()
                    == "sending"
                )
            restricted = _restrict(owner, ada)
            assert restricted.status_code == 200, restricted.text
        finally:
            finish.set()
        assert attempt.result(timeout=10) == expected
    with owner_engine.connect() as conn:
        marker = conn.execute(
            text("select restricted_at from outbox_messages where id=:i"), {"i": outbox_id}
        ).scalar_one()
        assert marker is not None
        assert (
            conn.execute(
                text("select count(*) from jobs where kind='notifications.deliver' and payload->>'outbox_id'=:i"),
                {"i": str(outbox_id)},
            ).scalar_one()
            == 1
        )
    if expected == "unknown":
        retry = owner.post(f"/api/v1/notifications/outbox/{outbox_id}/resolve", json={"resolution": "retry"})
        assert retry.status_code == 409 and retry.json()["code"] == "notifications.delivery_blocked"
        assert (
            owner.post(
                f"/api/v1/notifications/outbox/{outbox_id}/resolve", json={"resolution": "delivered"}
            ).status_code
            == 200
        )
    # A later status change or profile switch cannot make this message claimable again.
    with owner_engine.begin() as conn:
        conn.execute(text("update outbox_messages set status='pending', last_error=null where id=:i"), {"i": outbox_id})
    monkeypatch.setattr(get_settings(), "data_profile", "standard")
    assert notifications.deliver(CYCLE, outbox_id) == "skipped" and len(calls) == 1
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("select restricted_at from outbox_messages where id=:i"), {"i": outbox_id}).scalar_one()
            == marker
        )


@pytest.mark.parametrize("replacement", [None, "Ben Example"])
def test_restricted_conversation_cannot_be_relinked_or_replied_to(
    demo: Any, regulated: None, owner_engine: Engine, replacement: str | None
) -> None:
    owner = demo["owner"]
    ada = customer(owner, "Ada Example")
    other = customer(owner, replacement)["id"] if replacement else None
    conv = owner.post(
        "/api/v1/conversations", json={"channel": "phone_log", "customer_id": ada["id"], "body": "Retained record"}
    ).json()
    assert _restrict(owner, ada).status_code == 200
    current = owner.get(f"/api/v1/conversations/{conv['id']}").json()
    relink = owner.patch(
        f"/api/v1/conversations/{conv['id']}", json={"version": current["version"], "customer_id": other}
    )
    assert relink.status_code == 409 and relink.json()["code"] == "conversations.restricted", relink.text
    # The public helper is also used by scheduling executors; it must enforce the same rule.
    from app.modules.conversations import public as conversations
    from app.platform.context import Context
    from app.platform.db import transaction
    from app.platform.errors import Conflict

    ctx = Context(
        request_id="t", actor_kind="user", user_id=owner.user_id, business_id=CYCLE, permissions=owner.permissions
    )
    with pytest.raises(Conflict, match=r"conversations\.restricted"), transaction(ctx) as db:
        conversations.link_customer(db, ctx, UUID(conv["id"]), UUID(other) if other else None)
    with owner_engine.begin() as conn:
        assert (
            conn.execute(text("select restricted_at from conversations where id=:i"), {"i": conv["id"]}).scalar_one()
            is not None
        )
        # Even a link changed outside the application cannot remove the independent restriction.
        conn.execute(text("update conversations set customer_id=:c where id=:i"), {"c": other, "i": conv["id"]})
    reply = owner.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"direction": "outbound", "body": "Blocked"}
    )
    assert reply.status_code == 409 and reply.json()["code"] == "conversations.restricted"


def test_restriction_deletes_cached_customer_responses_without_erasing_history(
    demo: Any, regulated: None, owner_engine: Engine
) -> None:
    owner = demo["owner"]
    body = {"display_name": "Cache Example", "contact_points": [{"type": "email", "value": "cache@example.com"}]}
    headers = {"Idempotency-Key": "regulated-customer-cache"}
    original = owner.post("/api/v1/crm/customers", json=body, headers=headers)
    assert original.status_code == 201
    person = original.json()
    assert owner.post("/api/v1/crm/customers", json=body, headers=headers).json()["id"] == person["id"]
    assert owner.post(f"/api/v1/crm/customers/{person['id']}/notes", json={"body": "Keep this note"}).status_code == 201
    restricted = _restrict(owner, person)
    assert restricted.status_code == 200, restricted.text
    assert restricted.json()["report"]["platform"]["cached_responses_deleted"] == 1
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from idempotency_keys where response_body::text like :id"),
                {"id": f"%{person['id']}%"},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(text("select body from customer_notes where customer_id=:c"), {"c": person["id"]}).scalar_one()
            == "Keep this note"
        )
    # Deleting the cache makes this a new mutation; it must never replay the original active record.
    replay = owner.post("/api/v1/crm/customers", json=body, headers=headers)
    assert replay.status_code == 201 and replay.json()["id"] != person["id"]


def test_cached_unlinked_conversation_refreshes_its_restriction_before_relinking(demo: Any, regulated: None) -> None:
    from app.modules.conversations import public as conversations
    from app.modules.conversations.models import Conversation
    from app.platform.context import Context
    from app.platform.db import transaction
    from app.platform.errors import Conflict

    owner = demo["owner"]
    ada, ben = customer(owner, "Ada Example"), customer(owner, "Ben Example")
    started = owner.post(
        "/api/v1/public/example-cycle-works/chat",
        json={
            "name": "Ada Example",
            "email": "ada@example.com",
            "message": "Keep this conversation",
        },
    )
    assert started.status_code == 201, started.text
    cid = UUID(started.json()["conversation_id"])
    ctx = Context(
        request_id="stale-conversation",
        actor_kind="user",
        user_id=owner.user_id,
        business_id=CYCLE,
        permissions=owner.permissions,
    )
    with pytest.raises(Conflict, match=r"conversations\.restricted"), transaction(ctx) as db:
        cached = db.get(Conversation, cid)
        assert cached is not None and cached.customer_id is None and cached.restricted_at is None
        assert _restrict(owner, ada).status_code == 200
        # The restriction in a separate transaction linked and marked the visitor-only thread.
        # A caller already holding this ORM object must refresh under the mutation lock.
        conversations.link_customer(db, ctx, cid, UUID(ben["id"]))
