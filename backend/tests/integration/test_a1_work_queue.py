"""A1 unified work/exception projections, cursor ordering, permissions and tenant isolation."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, text

from app.modules.approvals.models import (
    ActionProposal,
    AutonomyEvaluation,
    AutonomyRule,
    ChatConfirmCounter,
    ChatConfirmReservation,
)
from app.modules.identity.testing import login_as
from app.modules.notifications.models import OutboxMessage
from app.modules.org import public as org
from app.modules.org.testing import make_business
from app.modules.purchasing import quote_service
from app.modules.purchasing.schemas_quotes import SendQuoteRequests
from app.platform import clock
from app.platform.context import system_context
from app.platform.db import transaction
from tests.integration.test_a1_chat_confirm import order, proposal
from tests.integration.test_a1_chat_confirm import shop as shop
from tests.integration.test_a1_send_delay import queued


def test_work_queue_and_exceptions_contents_pagination_and_rls(shop: Any) -> None:
    ident = queued(shop)
    p = proposal(shop, order(shop))
    ctx, client = shop["ctx"], shop["client"]
    with transaction(ctx) as db:
        request = quote_service.send_requests(
            db,
            ctx,
            SendQuoteRequests(
                lines=[{"item_id": shop["item"], "quantity": "10"}],
                supplier_ids=[shop["supplier"]],
                needed_by=clock.now().date() + timedelta(days=7),
                reply_by=clock.now().date() + timedelta(days=2),
            ),
            "queue-quotes",
        )
        request_id = request.id
    result = client.get("/api/v1/ai/work-queue?limit=100")
    assert result.status_code == 200, result.text
    items = result.json()["items"]
    assert {i["kind"] for i in items} == {"proposal", "outbound_mail", "supplier_quote"}
    pending = next(i for i in items if i["id"] == str(p["id"]))
    assert pending["approve_permission"] == "purchasing.po.approve"
    assert pending["waiting_on_roles"] == ["admin", "demo_reviewer", "owner"]
    mail = next(i for i in items if i["id"] == str(ident))
    assert mail["stop_until"] and mail["status"] == "pending"
    supplier = next(i for i in items if i["kind"] == "supplier_quote")
    assert supplier["quote_request_id"] == str(request_id) and supplier["due_date"]
    assert "parts@example.com" not in result.text and "quote_url" not in result.text
    paged, cursor = [], None
    for _ in range(10):
        page = client.get("/api/v1/ai/work-queue", params={"limit": 1, **({"cursor": cursor} if cursor else {})}).json()
        paged.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert [i["id"] for i in paged] == [i["id"] for i in items]
    with transaction(ctx) as db:
        db.get(ActionProposal, p["id"]).status = "failed"
        db.get(ActionProposal, p["id"]).failure_code = "purchasing.version_conflict"
        db.get(OutboxMessage, ident).status = "unknown"
        other_mail = db.scalars(select(OutboxMessage).where(OutboxMessage.id != ident)).one()
        other_mail.status = "failed"
        rule = AutonomyRule(
            business_id=ctx.business_id, action_type="inventory.create_reorder_draft", granted_by=ctx.user_id
        )
        db.add(rule)
        db.flush()
        db.add(
            AutonomyEvaluation(
                business_id=ctx.business_id,
                proposal_id=p["id"],
                rule_id=rule.id,
                rule_version=1,
                outcome="fell_back",
                reasons=["autonomy_paused"],
                created_at=clock.now(),
            )
        )
        org.set_autonomy_paused(db, ctx, True)
    result = client.get("/api/v1/ai/exceptions")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["paused"]
    assert {i["kind"] for i in data["items"]} == {"proposal", "outbound_mail", "autonomy_fallback"}
    assert {i["failure_code"] for i in data["items"] if i["failure_code"]} == {
        "purchasing.version_conflict",
        "notifications.delivery_unknown",
        "notifications.delivery_failed",
    }
    now = clock.now()
    clock.set_clock(lambda: now + timedelta(days=8))
    client = login_as(shop["app"], shop["business"], ["owner"], user_id=ctx.user_id)
    assert all(i["kind"] != "autonomy_fallback" for i in client.get("/api/v1/ai/exceptions").json()["items"])
    foreign_business = make_business(slug="foreign")
    foreign = login_as(shop["app"], foreign_business, ["owner"])
    for path in ("work-queue", "exceptions"):
        assert foreign.get(f"/api/v1/ai/{path}").json()["items"] == []
    with transaction(system_context(foreign_business)) as db:
        assert list(db.scalars(select(ChatConfirmCounter))) == []
        assert list(db.scalars(select(ChatConfirmReservation))) == []
        assert list(db.scalars(select(AutonomyEvaluation))) == []
    denied = login_as(shop["app"], shop["business"], [])
    assert denied.get("/api/v1/ai/work-queue").status_code == 403
    assert denied.get("/api/v1/ai/exceptions").status_code == 403
    # Proposal-read alone is the documented read API permission.
    read_ctx = dataclasses.replace(ctx, permissions=frozenset({"approvals.proposal.read"}))
    from app.modules.ai.work_queue_api import exceptions, work_queue

    assert exceptions(50, None, read_ctx).paused
    assert all(i.kind != "proposal" for i in work_queue(50, None, read_ctx).items)


def test_a1_new_tables_have_forced_rls_and_decision_provenance_is_immutable(shop: Any, owner_engine: Any) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "select relname,relrowsecurity,relforcerowsecurity from pg_class "
                "where relname in ('chat_confirm_counters','chat_confirm_reservations')"
            )
        ).all()
        assert len(rows) == 2 and all(row[1] and row[2] for row in rows)
        for privilege in ("UPDATE", "DELETE"):
            assert not conn.scalar(
                text("select has_table_privilege('smb_app','approval_decisions',:p)"), {"p": privilege}
            )
    other = make_business(slug="other")
    with transaction(shop["ctx"]) as db:
        db.add(ChatConfirmCounter(business_id=shop["business"], day=clock.now().date(), total_minor=1200))
    with transaction(system_context(other)) as db:
        assert list(db.scalars(select(ChatConfirmCounter))) == []
        assert list(db.scalars(select(ChatConfirmReservation))) == []


def test_activity_projects_delivery_and_policy_mail_gets_delay(shop: Any, registry: Any) -> None:
    import dataclasses

    from app.modules.approvals import engine
    from app.modules.approvals import service as approvals
    from app.modules.approvals.autonomy_schemas import RulePut
    from app.modules.approvals.autonomy_service import put_rule
    from app.modules.notifications import public as notifications
    from app.platform.context import assistant_context

    ctx, client = shop["ctx"], shop["client"]
    client.put(f"/api/v1/inventory/reorder-rules/{shop['item']}", json={"reorder_point": "2", "target_level": "10"})
    spec = registry.executors["inventory.create_reorder_draft"]

    def run(db, acting, payload, meta):
        result = spec.run(db, acting, payload, meta)
        notifications.enqueue_message(
            db,
            acting,
            channel="simulated",
            recipient="parts@example.com",
            template_key="test_message",
            variables={"name": "Example"},
            idempotency_key="policy-mail",
        )
        return result

    registry.executors[spec.action_type] = dataclasses.replace(spec, run=run)
    with transaction(ctx) as db:
        put_rule(db, ctx, RulePut(action_type=spec.action_type, level="act_notify", version=0))
    with transaction(ctx) as db:
        put_rule(db, ctx, RulePut(action_type=spec.action_type, level="act_notify", shadow=False, version=1))
    ai = assistant_context(ctx.business_id, ctx.permissions, on_behalf_of_user_id=None, ai_run_id=uuid.uuid4())
    with transaction(ai) as db:
        p = approvals.create_proposal(
            db,
            ai,
            action_type=spec.action_type,
            title="Restock Example cable",
            payload={"lines": [{"item_id": shop["item"], "quantity": "10"}]},
        )
    assert p.status == "approved"
    assert engine.execute(shop["business"], p.id, p.current_revision_id) == "succeeded"
    activity = client.get("/api/v1/ai/autonomy/activity").json()["items"]
    assert len(activity) == 1 and activity[0]["status"] == "succeeded" and activity[0]["delivery_state"] == "scheduled"
    with transaction(ctx) as db:
        row = db.scalars(select(OutboxMessage)).one()
        assert row.not_before == clock.now() + timedelta(seconds=120)
        assert row.proposal_id == p.id
