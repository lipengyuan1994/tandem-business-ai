"""M08 approvals tests (docs/roadmap/modules/M08-approvals.md#tests-must-exist)."""

from __future__ import annotations

import dataclasses
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.approvals import engine
from app.modules.approvals.testing_executor import SPEC
from app.modules.identity.testing import add_member, login_as, make_user
from app.modules.org import public as org
from app.platform import clock, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.ids import new_id
from app.platform.registry import Registry

TYPE = "testing.noop_write"


def _business(slug: str = "example-cycle-works") -> uuid.UUID:
    business_id = new_id()
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business_id)
    with transaction(ctx) as db:
        org.create_business(db, ctx, name=slug, slug=slug, timezone="America/Denver", currency="USD", locale="en-US")
    return business_id


@pytest.fixture
def app(registry: Registry) -> Any:
    return create_app(registry)


@pytest.fixture
def biz() -> uuid.UUID:
    return _business()


def _propose(client: Any, **payload: Any) -> dict[str, Any]:
    body = {"action_type": TYPE, "title": "Add test item", "payload": {"name": "Brake pads", **payload}}
    response = client.post("/api/v1/approvals/proposals", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _approve(client: Any, proposal: dict[str, Any]) -> Any:
    rev = proposal["current_revision"]
    return client.post(
        f"/api/v1/approvals/proposals/{proposal['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )


def _count(engine_: Engine, sql: str) -> int:
    with engine_.connect() as conn:
        return int(conn.execute(text(sql)).scalar_one())


def test_create_validates_payload_and_builds_preview(app: Any, biz: uuid.UUID) -> None:
    staff = login_as(app, biz, ["operator"])
    p = _propose(staff)
    assert p["status"] == "pending_review" and p["origin_kind"] == "staff"
    assert p["current_revision"]["preview"] == [{"label": "Test item", "before": None, "after": "Brake pads"}]
    assert len(p["current_revision"]["payload_hash"]) == 64
    bad = staff.post("/api/v1/approvals/proposals", json={"action_type": TYPE, "title": "x", "payload": {"name": ""}})
    assert bad.status_code == 422 and bad.json()["code"] == "approvals.invalid_payload"
    unknown = staff.post(
        "/api/v1/approvals/proposals", json={"action_type": "nope.nothing", "title": "x", "payload": {}}
    )
    assert unknown.status_code == 422 and unknown.json()["code"] == "approvals.unknown_action_type"


def test_edit_creates_revision_and_old_hash_cannot_be_approved(app: Any, biz: uuid.UUID) -> None:
    creator, approver = login_as(app, biz, ["operator"]), login_as(app, biz, ["admin"])
    p = _propose(creator)
    old = p["current_revision"]
    edited = approver.post(
        f"/api/v1/approvals/proposals/{p['id']}/revisions",
        json={"base_revision_id": old["id"], "payload": {"name": "Brake pads (pair)"}},
    ).json()
    assert (
        edited["current_revision"]["revision_no"] == 2
        and edited["current_revision"]["payload_hash"] != old["payload_hash"]
    )
    stale = _approve(approver, p)  # still points at revision 1
    assert stale.status_code == 409 and stale.json()["code"] == "approvals.revision_changed"
    stale_edit = creator.post(
        f"/api/v1/approvals/proposals/{p['id']}/revisions",
        json={"base_revision_id": old["id"], "payload": {"name": "x"}},
    )
    assert stale_edit.status_code == 409
    assert _approve(approver, edited).json()["status"] == "approved"


def test_self_approval_blocked_unless_setting_enabled(app: Any, biz: uuid.UUID) -> None:
    owner = login_as(app, biz, ["owner"])
    p = _propose(owner)
    blocked = _approve(owner, p)
    assert blocked.status_code == 403 and blocked.json()["code"] == "approvals.self_approval_forbidden"
    settings = owner.get("/api/v1/org/settings").json()
    owner.patch(
        "/api/v1/org/settings",
        json={"version": settings["version"], "settings": {**settings["settings"], "allow_self_approval": True}},
    )
    assert _approve(owner, p).json()["status"] == "approved"


def test_assistant_proposal_on_behalf_cannot_be_self_approved(app: Any, biz: uuid.UUID) -> None:
    from app.modules.approvals import public as approvals
    from app.modules.approvals import service
    from app.platform.context import assistant_context
    from app.platform.testing.auth import ctx_for

    owner = login_as(app, biz, ["owner"])
    ctx = assistant_context(
        biz, frozenset({"ai.assistant.use"}), on_behalf_of_user_id=owner.user_id, ai_run_id=new_id()
    )
    with transaction(ctx) as db:
        ref = approvals.create_proposal(db, ctx, action_type=TYPE, payload={"name": "Brake pads"}, title="Restock")
    response = owner.get(f"/api/v1/approvals/proposals/{ref.id}")
    assert response.status_code == 200
    p = response.json()
    assert p["origin_kind"] == "assistant" and p["origin_user_id"] == str(owner.user_id)
    # Requesters retain the existing revision access even without the action's approval permission.
    requester = ctx_for(biz, ["approvals.proposal.read"], user_id=owner.user_id)
    with transaction(requester) as db:
        assert service.get_detail(db, requester, ref.id)["proposal"].id == ref.id
        service.revise(db, requester, ref.id, uuid.UUID(p["current_revision"]["id"]), {"name": "Brake pads (pair)"})
    p = owner.get(f"/api/v1/approvals/proposals/{ref.id}").json()
    blocked = _approve(owner, p)
    assert blocked.status_code == 403 and blocked.json()["code"] == "approvals.self_approval_forbidden"
    settings = owner.get("/api/v1/org/settings").json()
    updated = owner.patch(
        "/api/v1/org/settings",
        json={"version": settings["version"], "settings": {**settings["settings"], "allow_self_approval": True}},
    )
    assert updated.status_code == 200
    assert _approve(owner, p).json()["status"] == "approved"


def test_find_open_proposals_filters(app: Any, biz: uuid.UUID, owner_engine: Engine) -> None:
    from app.modules.approvals import public as approvals
    from app.platform.errors import Forbidden
    from app.platform.testing.auth import ctx_for

    creator = login_as(app, biz, ["operator"])
    pending, approved, closed = (_propose(creator) for _ in range(3))
    assert _approve(login_as(app, biz, ["admin"]), approved).status_code == 200
    conversation = new_id()
    with owner_engine.begin() as conn:
        conn.execute(
            text("update action_proposals set conversation_id=:c where id=:i"), {"c": conversation, "i": pending["id"]}
        )
        conn.execute(text("update action_proposals set status='rejected' where id=:i"), {"i": closed["id"]})
    other = _business("other-shop")
    _propose(login_as(app, other, ["operator"]))
    for permission in ("approvals.proposal.read", "ai.assistant.use"):
        ctx = ctx_for(biz, [permission])
        with transaction(ctx) as db:
            rows = approvals.find_open_proposals(db, ctx)
            assert {r.id for r in rows} == {uuid.UUID(pending["id"]), uuid.UUID(approved["id"])}
            assert all(
                r.action_type == TYPE
                and r.title == "Add test item"
                and r.origin_kind == "staff"
                and r.created_at is not None
                for r in rows
            )
            assert [r.id for r in approvals.find_open_proposals(db, ctx, conversation_id=conversation)] == [
                uuid.UUID(pending["id"])
            ]
            assert len(approvals.find_open_proposals(db, ctx, action_types=[TYPE])) == 2
            assert approvals.find_open_proposals(db, ctx, action_types=["crm.create_customer"]) == []
            assert approvals.find_open_proposals(db, ctx, action_types=[]) == []
            assert len(approvals.find_open_proposals(db, ctx, conversation_id=conversation, action_types=[TYPE])) == 1
    ctx = ctx_for(biz)
    with transaction(ctx) as db, pytest.raises(Forbidden):
        approvals.find_open_proposals(db, ctx)


def test_approver_needs_type_permission(app: Any, biz: uuid.UUID) -> None:
    p = _propose(login_as(app, biz, ["operator"]))
    stock_keeper = login_as(app, biz, ["inventory_manager"])  # lacks crm.proposal.approve
    response = _approve(stock_keeper, p)
    assert response.status_code == 403 and response.json()["code"] == "approvals.not_authorized_to_approve"


@pytest.mark.concurrency
def test_double_approve_click_executes_once(app: Any, biz: uuid.UUID, registry: Registry, owner_engine: Engine) -> None:
    p = _propose(login_as(app, biz, ["operator"]), write_test_item=True)
    approvers = [login_as(app, biz, ["admin"]) for _ in range(4)]
    barrier, codes = threading.Barrier(4), []

    def click(c: Any) -> None:
        barrier.wait()
        codes.append(_approve(c, p).status_code)

    threads = [threading.Thread(target=click, args=(c,)) for c in approvers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409, 409, 409]
    assert _count(owner_engine, "select count(*) from approval_decisions") == 1
    jobs.run_pending(registry)
    assert _count(owner_engine, "select count(*) from test_items") == 1


@pytest.mark.concurrency
def test_two_workers_cannot_both_execute(app: Any, biz: uuid.UUID, owner_engine: Engine) -> None:
    p = _propose(login_as(app, biz, ["operator"]), write_test_item=True)
    _approve(login_as(app, biz, ["admin"]), p)
    rev = uuid.UUID(p["current_revision"]["id"])
    barrier, results = threading.Barrier(5), []

    def worker() -> None:
        barrier.wait()
        results.append(engine.execute(biz, uuid.UUID(p["id"]), rev))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("succeeded") == 1
    assert _count(owner_engine, "select count(*) from test_items") == 1
    assert _count(owner_engine, "select count(*) from execution_attempts") == 1


def test_execution_fails_cleanly_when_approver_role_revoked_before_run(
    app: Any, biz: uuid.UUID, registry: Registry, owner_engine: Engine
) -> None:
    owner = login_as(app, biz, ["owner"])
    approver_id = make_user("riley@example.com")
    membership = add_member(approver_id, biz, ["admin"])
    approver = login_as(app, biz, ["admin"], user_id=approver_id)
    p = _propose(login_as(app, biz, ["operator"]), write_test_item=True)
    assert _approve(approver, p).status_code == 200
    owner.put(f"/api/v1/identity/members/{membership}/roles", json={"roles": ["auditor"]})  # before the worker runs
    jobs.run_pending(registry)
    detail = owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()
    assert detail["status"] == "failed" and detail["failure_code"] == "approvals.approver_no_longer_authorized"
    assert _count(owner_engine, "select count(*) from test_items") == 0


def test_execution_business_error_rolls_back_all_local_effects(
    app: Any, biz: uuid.UUID, registry: Registry, owner_engine: Engine
) -> None:
    owner = login_as(app, biz, ["owner"])
    p = _propose(login_as(app, biz, ["operator"]), write_test_item=True, fail=True)
    _approve(owner, p)
    jobs.run_pending(registry)
    detail = owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()
    assert detail["status"] == "failed" and detail["failure_code"] == "testing.business_rule_failed"
    assert detail["attempts"][0]["status"] == "failed"
    assert _count(owner_engine, "select count(*) from test_items") == 0
    assert _count(owner_engine, "select count(*) from audit_events where action = 'testing.noop.executed'") == 0
    assert _count(owner_engine, "select count(*) from audit_events where action = 'approvals.proposal.failed'") == 1


def test_crash_mid_execution_then_retry_succeeds_once(
    app: Any, biz: uuid.UUID, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def flaky_run(db: Any, ctx: Any, payload: Any, meta: Any) -> dict[str, Any]:
        calls["n"] += 1
        result = SPEC.run(db, ctx, payload, meta)  # writes the test item and an audit row...
        if calls["n"] == 1:
            raise RuntimeError("process killed")  # ...then dies before commit
        return result

    flaky = dataclasses.replace(SPEC, run=flaky_run)
    monkeypatch.setattr(engine, "executor_for", lambda action_type: flaky)
    start = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    clock.set_clock(lambda: start)
    owner = login_as(app, biz, ["owner"])
    p = _propose(login_as(app, biz, ["operator"]), write_test_item=True)
    _approve(owner, p)
    jobs.run_pending(registry)  # crash: job requeued with backoff
    assert owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()["status"] == "executing"
    assert _count(owner_engine, "select count(*) from test_items") == 0
    clock.set_clock(lambda: start + timedelta(minutes=1))  # past the retry backoff, within the session idle limit
    jobs.run_pending(registry)  # resume path
    detail = owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()
    assert detail["status"] == "succeeded" and calls["n"] == 2
    assert _count(owner_engine, "select count(*) from test_items") == 1
    assert _count(owner_engine, "select count(*) from execution_attempts where status = 'succeeded'") == 1


def test_expiry_job_expires_and_publishes_closed(
    app: Any, biz: uuid.UUID, registry: Registry, owner_engine: Engine
) -> None:
    start = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    clock.set_clock(lambda: start)
    p = _propose(login_as(app, biz, ["operator"]))
    clock.set_clock(lambda: start + timedelta(hours=25))
    jobs.enqueue_cron(registry)
    jobs.run_pending(registry)
    with owner_engine.connect() as conn:
        status = conn.execute(text("select status from action_proposals where id = :i"), {"i": p["id"]}).scalar_one()
        closed = (
            conn.execute(text("select payload->>'outcome' from domain_events where type = 'approvals.proposal_closed'"))
            .scalars()
            .all()
        )
    assert status == "expired" and closed == ["expired"]


def test_cancel_and_reject_close_the_proposal(app: Any, biz: uuid.UUID, owner_engine: Engine) -> None:
    creator, admin = login_as(app, biz, ["operator"]), login_as(app, biz, ["admin"])
    first, second = _propose(creator), _propose(creator)
    assert creator.post(f"/api/v1/approvals/proposals/{first['id']}/cancel").json()["status"] == "cancelled"
    rev = second["current_revision"]
    rejected = admin.post(
        f"/api/v1/approvals/proposals/{second['id']}/decisions",
        json={
            "decision": "reject",
            "revision_id": rev["id"],
            "payload_hash": rev["payload_hash"],
            "comment": "not needed",
        },
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["decisions"][0]["comment"] == "not needed"
    assert _count(owner_engine, "select count(*) from jobs where kind = 'approvals.execute'") == 0


def test_cross_business_proposal_not_visible(app: Any, biz: uuid.UUID) -> None:
    p = _propose(login_as(app, biz, ["operator"]))
    other = login_as(app, _business("example-garden-store"), ["owner"])
    assert other.get(f"/api/v1/approvals/proposals/{p['id']}").status_code == 404
    assert other.get("/api/v1/approvals/proposals").json()["items"] == []
    assert _approve(other, p).status_code == 404


def test_queue_filters(app: Any, biz: uuid.UUID) -> None:
    creator = login_as(app, biz, ["operator"])
    _propose(creator)
    _propose(creator)
    admin, stock = login_as(app, biz, ["admin"]), login_as(app, biz, ["inventory_manager"])
    assert len(admin.get("/api/v1/approvals/proposals", params={"can_approve": True}).json()["items"]) == 2
    assert stock.get("/api/v1/approvals/proposals", params={"can_approve": True}).json()["items"] == []
    assert len(creator.get("/api/v1/approvals/proposals", params={"mine": True}).json()["items"]) == 2
    types = admin.get("/api/v1/approvals/action-types").json()
    noop = next(t for t in types if t["action_type"] == TYPE)
    assert "name" in noop["payload_schema"]["properties"]
