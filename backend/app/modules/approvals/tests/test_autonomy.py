"""P10-A0: real-DB delegated approvals, caps, revocation, tenancy and API."""

from __future__ import annotations

import dataclasses
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, select, text

from app.main import create_app
from app.modules.approvals import autonomy_service as rules
from app.modules.approvals import engine, service
from app.modules.approvals.autonomy_schemas import RulePut
from app.modules.approvals.models import ApprovalDecision, AutonomyCounter, AutonomyEvaluation, AutonomyRule
from app.modules.identity import public as identity
from app.modules.identity.testing import add_member, login_as, make_user
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.context import Context, assistant_context, system_context
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.registry import Registry

ACTION = "inventory.create_reorder_draft"
BASE = "/api/v1/ai/autonomy"


@pytest.fixture
def setup(registry: Registry) -> tuple[uuid.UUID, Context, uuid.UUID]:
    business = make_business()
    owner = make_user()
    add_member(owner, business, ["owner"])
    ctx = Context("test", "user", owner, business, identity.ROLES["owner"])
    client = login_as(create_app(registry), business, ["owner"], user_id=owner)
    item = add_item(client, "Example cable")
    return business, ctx, item


def add_item(client: Any, name: str) -> uuid.UUID:
    response = client.post(
        "/api/v1/catalog/items",
        json={
            "kind": "product",
            "name": name,
            "unit": "each",
            "price_minor": 100,
            "is_stock_tracked": True,
        },
    )
    assert response.status_code == 201, response.text
    item = uuid.UUID(response.json()["id"])
    response = client.put(f"/api/v1/inventory/reorder-rules/{item}", json={"reorder_point": "2", "target_level": "5"})
    assert response.status_code == 200, response.text
    return item


def ai_ctx(business: uuid.UUID) -> Context:
    return assistant_context(
        business,
        frozenset({"ai.assistant.use", "inventory.stock.read"}),
        on_behalf_of_user_id=None,
        ai_run_id=uuid.uuid4(),
    )


def rule(ctx: Context, **changes: Any) -> AutonomyRule:
    with transaction(ctx) as db:
        current = db.scalar(select(AutonomyRule).where(AutonomyRule.business_id == ctx.business_id))
        body = {
            "action_type": ACTION,
            "level": "act_notify",
            "shadow": True,
            "daily_cap": 10,
            "active": True,
            "version": current.version if current else 0,
            **changes,
        }
        saved = rules.put_rule(db, ctx, RulePut(**body))
    return saved


def live(ctx: Context, **changes: Any) -> AutonomyRule:
    rule(ctx)
    return rule(ctx, shadow=False, **changes)


def propose(business: uuid.UUID, item: uuid.UUID, ctx: Context | None = None) -> Any:
    ctx = ctx or ai_ctx(business)
    with transaction(ctx) as db:
        return service.create_proposal(
            db,
            ctx,
            action_type=ACTION,
            title="Reorder example cable",
            payload={
                "lines": [{"item_id": str(item), "quantity": "5"}],
            },
        )


def evaluation(business: uuid.UUID, proposal: Any) -> AutonomyEvaluation:
    with transaction(system_context(business)) as db:
        row = db.scalar(select(AutonomyEvaluation).where(AutonomyEvaluation.proposal_id == proposal.id))
        assert row is not None
        return row


def test_manual_never_uses_rule(setup: Any) -> None:
    business, ctx, item = setup
    live(ctx)
    p = propose(business, item, ctx)
    assert p.status == "pending_review"
    with transaction(ctx) as db:
        assert list(db.scalars(select(ApprovalDecision))) == []
        assert list(db.scalars(select(AutonomyEvaluation))) == []


def test_policy_executes_with_real_delegation_and_activity(setup: Any, registry: Registry) -> None:
    business, ctx, item = setup
    saved = live(ctx)
    p = propose(business, item)
    assert p.status == "approved"
    with transaction(ctx) as db:
        decision = db.scalars(select(ApprovalDecision)).one()
        assert (decision.decided_by_kind, decision.decided_by) == ("policy", None)
        assert (decision.rule_id, decision.rule_version, decision.granted_by) == (saved.id, saved.version, ctx.user_id)
        assert decision.roles_at_decision == ["owner"]
    assert evaluation(business, p).outcome == "acted"
    assert engine.execute(business, p.id, p.current_revision_id) == "succeeded"
    assert engine.execute(business, p.id, p.current_revision_id) == "done"
    app = create_app(registry)
    client = login_as(app, business, ["owner"], user_id=ctx.user_id)
    activity = client.get(BASE + "/activity").json()["items"]
    assert len(activity) == 1 and activity[0]["status"] == "succeeded"
    assert activity[0]["reorder_draft_id"] and activity[0]["result_link"]
    result = client.get(activity[0]["result_link"])
    assert result.status_code == 200 and result.json()["id"] == activity[0]["reorder_draft_id"]
    detail = client.get(f"/api/v1/approvals/proposals/{p.id}")
    assert detail.status_code == 200 and detail.json()["decisions"][0]["decided_by"] is None
    with transaction(ctx) as db:
        rows = db.execute(
            text("select summary,actor_kind,on_behalf_of_user_id from audit_events where proposal_id=:p"), {"p": p.id}
        ).all()
        assert any("approved by autonomy rule" in r.summary.lower() for r in rows)
        executed = [r for r in rows if "Executed:" in r.summary]
        assert len(executed) == 1 and "autonomy rule" in executed[0].summary
        assert executed[0].actor_kind == "system" and executed[0].on_behalf_of_user_id == ctx.user_id


@pytest.mark.parametrize(
    "level,shadow,outcome,reason",
    [
        ("act_notify", True, "would_act", None),
        ("act", True, "would_act", None),
        ("draft", False, "fell_back", "level_not_supported"),
    ],
)
def test_shadow_and_unsupported_level(setup: Any, level: str, shadow: bool, outcome: str, reason: str | None) -> None:
    business, ctx, item = setup
    rule(ctx, level=level)
    if not shadow:
        rule(ctx, level=level, shadow=False)
    p = propose(business, item)
    assert p.status == "pending_review"
    ev = evaluation(business, p)
    assert ev.outcome == outcome and (reason is None or reason in ev.reasons)
    with transaction(ctx) as db:
        assert list(db.scalars(select(ApprovalDecision))) == []


@pytest.mark.parametrize("with_rule", [False, True])
def test_ask_or_missing_rule_unchanged(setup: Any, with_rule: bool) -> None:
    business, ctx, item = setup
    if with_rule:
        rule(ctx, level="ask")
    assert propose(business, item).status == "pending_review"
    with transaction(ctx) as db:
        assert list(db.scalars(select(AutonomyEvaluation))) == []


def test_off_refuses_ai_but_not_staff(setup: Any) -> None:
    business, ctx, item = setup
    rule(ctx, level="off")
    with pytest.raises(DomainError, match=r"approvals\.autonomy_off"):
        propose(business, item)
    assert propose(business, item, ctx).status == "pending_review"


@pytest.mark.parametrize("when", ["creation", "execution"])
@pytest.mark.parametrize("change", ["pause", "roles", "inactive_member", "rule", "inactive_rule", "shadow"])
def test_revocation(setup: Any, owner_engine: Engine, when: str, change: str) -> None:
    business, ctx, item = setup
    live(ctx)
    p = propose(business, item) if when == "execution" else None
    if change == "pause":
        with transaction(ctx) as db:
            org.set_autonomy_paused(db, ctx, True)
    elif change in {"roles", "inactive_member"}:
        with owner_engine.begin() as db:
            if change == "roles":
                db.execute(text("delete from role_grants where business_id=:b"), {"b": business})
            else:
                db.execute(text("update memberships set status='suspended' where business_id=:b"), {"b": business})
    else:
        rule(
            ctx,
            level="ask" if change == "rule" else "act_notify",
            active=change != "inactive_rule",
            shadow=change == "shadow",
        )
    if p is None:
        p = propose(business, item)
        assert p.status == "pending_review"
        if change != "rule":
            assert evaluation(business, p).outcome == ("would_act" if change == "shadow" else "fell_back")
    else:
        assert engine.execute(business, p.id, p.current_revision_id) == "failed"
        with transaction(ctx) as db:
            assert db.get(service.ActionProposal, p.id).failure_code == "approvals.autonomy_revoked"
    with transaction(ctx) as db:
        assert db.execute(text("select count(*) from reorder_drafts")).scalar_one() == 0


def test_concurrent_daily_cap_and_local_day(setup: Any, registry: Registry) -> None:
    business, ctx, item = setup
    live(ctx, daily_cap=3)
    clock.set_clock(lambda: datetime(2026, 11, 6, 1, tzinfo=UTC))  # Nov 5 in Denver
    # Distinct items avoid accidentally serializing the cap test on the domain-intent lock.
    client = login_as(create_app(registry), business, ["owner"], user_id=ctx.user_id)
    items = [item, *[add_item(client, f"Example cable {n}") for n in range(5)]]
    with ThreadPoolExecutor(max_workers=6) as pool:
        proposals = list(pool.map(lambda item_id: propose(business, item_id), items))
    evs = [evaluation(business, p) for p in proposals]
    assert sum(e.outcome == "acted" for e in evs) == 3
    assert sum(e.outcome == "fell_back" and e.reasons == ["cap_reached"] for e in evs) == 3
    with transaction(ctx) as db:
        counter = db.scalars(select(AutonomyCounter)).one()
        assert counter.count == 3 and counter.day.isoformat() == "2026-11-05"
    clock.set_clock(lambda: datetime(2026, 11, 6, 8, tzinfo=UTC))
    assert propose(business, item).status == "approved"


def test_open_draft_blocks_proposal_and_executor(setup: Any, registry: Registry) -> None:
    business, ctx, item = setup
    live(ctx)
    p = propose(business, item)
    client = login_as(create_app(registry), business, ["owner"], user_id=ctx.user_id)
    assert (
        client.post(
            "/api/v1/inventory/reorder-drafts",
            json={
                "lines": [{"item_id": str(item), "quantity": "5"}],
            },
        ).status_code
        == 201
    )
    with transaction(ctx) as db:
        assert inventory.has_open_reorder_draft(db, ctx, item)
    with pytest.raises(DomainError, match=r"inventory\.open_reorder_draft"):
        propose(business, item)
    assert engine.execute(business, p.id, p.current_revision_id) == "failed"
    with transaction(ctx) as db:
        assert db.execute(text("select count(*) from reorder_drafts")).scalar_one() == 1


def test_stale_stock_fails(setup: Any) -> None:
    business, ctx, item = setup
    live(ctx)
    p = propose(business, item)
    with transaction(ctx) as db:
        inventory.record_movement(db, ctx, inventory.MovementIn(item_id=item, quantity=Decimal(10), reason="receipt"))
    assert engine.execute(business, p.id, p.current_revision_id) == "failed"
    with transaction(ctx) as db:
        assert db.get(service.ActionProposal, p.id).failure_code == "inventory.reorder_changed"
        assert db.execute(text("select count(*) from reorder_drafts")).scalar_one() == 0


def test_crash_after_claim_rolls_back_and_resumes_once(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    business, ctx, item = setup
    live(ctx)
    p = propose(business, item)
    original = service.executor_for(ACTION)

    def crash(*args: Any, **kwargs: Any) -> Any:
        original.run(*args, **kwargs)
        raise RuntimeError("crash after draft write")

    monkeypatch.setattr(engine, "executor_for", lambda _: dataclasses.replace(original, run=crash))
    with pytest.raises(RuntimeError):
        engine.execute(business, p.id, p.current_revision_id)
    with transaction(ctx) as db:
        assert db.get(service.ActionProposal, p.id).status == "executing"
        assert db.execute(text("select count(*) from reorder_drafts")).scalar_one() == 0
    monkeypatch.setattr(engine, "executor_for", lambda _: original)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: engine.execute(business, p.id, p.current_revision_id), range(2)))
    assert sorted(results) == ["done", "succeeded"]
    with transaction(ctx) as db:
        assert db.execute(text("select count(*) from reorder_drafts")).scalar_one() == 1


def test_api_permissions_version_conflict_and_rls(setup: Any, registry: Registry) -> None:
    business, ctx, item = setup
    app = create_app(registry)
    owner = login_as(app, business, ["owner"], user_id=ctx.user_id)
    operator = login_as(app, business, ["operator"])
    body = {
        "action_type": ACTION,
        "version": 0,
        "level": "act_notify",
        "shadow": False,
        "daily_cap": 10,
        "active": True,
    }
    for path in ("/rules", "/pause", "/resume"):
        response = operator.put(BASE + path, json=body) if path == "/rules" else operator.post(BASE + path)
        assert response.status_code == 403
    assert operator.get(BASE + "/activity").status_code == 200
    defaults = owner.get(BASE + "/rules").json()
    assert defaults["paused"] is False and defaults["items"][0]["version"] == 0
    saved = owner.put(BASE + "/rules", json=body)
    assert saved.status_code == 200 and saved.json()["shadow"] is True  # new rules start shadow
    assert owner.put(BASE + "/rules", json=body).status_code == 409
    assert owner.put(BASE + "/rules", json={**body, "version": 1}).status_code == 200
    p = propose(business, item)
    assert owner.get(BASE + "/rules").json()["items"][0]["summary"]["acted"] == 1
    assert owner.post(BASE + "/pause").json() == {"paused": True}
    assert owner.post(BASE + "/resume").json() == {"paused": False}
    other = make_business("other-example")
    foreign = login_as(app, other, ["owner"])
    assert foreign.get(BASE + "/rules").json()["items"][0]["id"] is None
    assert foreign.get(BASE + "/activity").json()["items"] == []
    with transaction(system_context(other)) as db:
        for model in (AutonomyRule, AutonomyEvaluation, AutonomyCounter):
            assert list(db.scalars(select(model))) == []
        assert db.get(service.ActionProposal, p.id) is None


def test_duplicate_ai_lines_cannot_inflate_quantity(setup: Any) -> None:
    business, ctx, item = setup
    live(ctx)
    ai = ai_ctx(business)
    with pytest.raises(DomainError, match=r"inventory\.reorder_changed"), transaction(ai) as db:
        service.create_proposal(
            db,
            ai,
            action_type=ACTION,
            title="Duplicate lines",
            payload={
                "lines": [{"item_id": str(item), "quantity": "5"}] * 2,
            },
        )
    with transaction(ctx) as db:
        assert list(db.scalars(select(ApprovalDecision))) == []


def test_transaction_rollback_does_not_spend_cap_or_publish(setup: Any) -> None:
    business, ctx, item = setup
    live(ctx, daily_cap=1)
    ai = ai_ctx(business)
    with pytest.raises(RuntimeError, match="rollback"), transaction(ai) as db:
        service.create_proposal(
            db,
            ai,
            action_type=ACTION,
            title="Rollback",
            payload={
                "lines": [{"item_id": str(item), "quantity": "5"}],
            },
        )
        raise RuntimeError("rollback")
    with transaction(ctx) as db:
        assert list(db.scalars(select(AutonomyCounter))) == []
        assert list(db.scalars(select(ApprovalDecision))) == []
        assert list(db.scalars(select(AutonomyEvaluation))) == []
        assert db.execute(text("select count(*) from jobs where kind='approvals.execute'")).scalar_one() == 0
    assert propose(business, item).status == "approved"


def test_activity_pagination_shadow_summary_and_admin_access(setup: Any, registry: Registry) -> None:
    business, ctx, item = setup
    rule(ctx)
    first, second = propose(business, item), propose(business, item)
    app = create_app(registry)
    client = login_as(app, business, ["admin"])
    summary = client.get(BASE + "/rules").json()["items"][0]["summary"]
    assert summary == {"would_act": 2, "acted": 0, "fell_back": 0}
    page1 = client.get(BASE + "/activity?limit=1").json()
    page2 = client.get(BASE + "/activity", params={"limit": 1, "cursor": page1["next_cursor"]}).json()
    assert page2["next_cursor"] is None
    assert {page1["items"][0]["proposal_id"], page2["items"][0]["proposal_id"]} == {str(first.id), str(second.id)}
    assert page1["items"][0]["proposal_id"] == str(second.id)
    bad = client.put(BASE + "/rules", json={"action_type": "inventory.adjust_stock", "level": "act", "version": 0})
    assert bad.status_code == 422 and bad.json()["code"] == "approvals.autonomy_action_not_eligible"
    assert client.post(BASE + "/pause").status_code == 200


def test_evaluations_and_decisions_are_append_only(setup: Any) -> None:
    from sqlalchemy.exc import DBAPIError

    business, ctx, item = setup
    live(ctx)
    propose(business, item)
    for table in ("autonomy_evaluations", "approval_decisions"):
        with pytest.raises(DBAPIError), transaction(ctx) as db:
            db.execute(text(f"delete from {table}"))  # noqa: S608 — fixed internal allowlist


@pytest.mark.parametrize("shadow", [False, True])
@pytest.mark.parametrize("legacy_audit", [False, True])
def test_activity_keeps_original_grantor_after_rule_edit(
    setup: Any, registry: Registry, owner_engine: Engine, shadow: bool, legacy_audit: bool
) -> None:
    business, ctx, item = setup
    saved = rule(ctx) if shadow else live(ctx)
    proposal = propose(business, item)
    with transaction(ctx) as db:
        original_name = identity.get_user_brief(db, ctx, ctx.user_id).display_name
    if legacy_audit:
        # Simulate pre-part-2 audit rows that lack the new structured rule snapshot.
        with owner_engine.begin() as conn:
            conn.execute(
                text("update audit_events set changes='{}'::jsonb where action='approvals.autonomy_rule.saved'"),
            )
    new_grantor = make_user(name="New Grantor Example")
    add_member(new_grantor, business, ["admin"])
    new_ctx = dataclasses.replace(ctx, user_id=new_grantor, permissions=identity.ROLES["admin"])
    rule(new_ctx, level="ask")
    client = login_as(create_app(registry), business, ["owner"], user_id=ctx.user_id)
    entry = client.get(BASE + "/activity").json()["items"][0]
    assert entry["rule_version"] == saved.version
    assert entry["action_type"] == ACTION
    assert entry["granted_by"] == str(ctx.user_id)
    assert entry["granted_by_name"] == original_name
    if not shadow:
        decision = client.get(f"/api/v1/approvals/proposals/{proposal.id}").json()["decisions"][0]
        assert decision["decided_by"] is None and decision["decided_by_name"] == ""
        assert decision["granted_by_name"] == original_name
