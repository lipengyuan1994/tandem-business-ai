"""Independent-review regressions: domain consequences, durable extraction and exact crash recovery."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.modules.ai.agents import AGENTS
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.models import AiRun
from app.modules.ai.permissions import AI_SYSTEM_PERMISSIONS
from app.modules.ai.schemas import ReorderNote
from app.modules.ai.team_models import ChainBudget
from app.modules.ai.team_schemas import AgentPatch
from app.modules.ai.team_settings import patch_settings
from app.modules.ai.tests.helpers import fake_router_by_class, insert_run
from app.modules.ai.tests.test_procurement import quote_script
from app.modules.ai.tests.test_reorder_note import NOTE, autonomy_rule
from app.modules.ai.tests.tool_helpers import seed_demo
from app.modules.ai.usecases import reorder_note
from app.modules.approvals import engine, service
from app.modules.approvals.models import ActionProposal, ExecutionAttempt, ProposalRevision
from app.modules.inventory import public as inventory
from app.modules.purchasing import service_suppliers
from app.modules.purchasing.schemas import SupplierIn, SupplierItemIn
from app.modules.scheduling import public as scheduling
from app.platform import clock, events, jobs
from app.platform.context import assistant_context
from app.platform.db import transaction
from app.platform.models import DomainEvent, Job
from tests.integration.test_a1_chat_confirm import shop as shop
from tests.integration.test_ai_team import owner_for


@pytest.fixture(autouse=True)
def clean_router():
    reset_router_cache()
    yield
    reset_router_cache()


def origin(ctx, agent, use_case, correlation_id, depth=1):
    run_id = insert_run(
        ctx, use_case=use_case, agent_key=agent, correlation_id=correlation_id, chain_depth=depth, status="succeeded"
    )
    return replace(
        assistant_context(ctx.business_id, AI_SYSTEM_PERMISSIONS, on_behalf_of_user_id=None, ai_run_id=run_id),
        agent_key=agent,
        correlation_id=correlation_id,
        chain_depth=depth,
    )


def approve(db, ctx, proposal):
    revision = db.get(ProposalRevision, proposal.current_revision_id)
    service.decide(
        db,
        ctx,
        proposal.id,
        decision="approve",
        revision_id=revision.id,
        payload_hash=revision.payload_hash,
        comment=None,
    )
    return revision.id


@pytest.mark.parametrize("depth", [1, 6])
def test_ai_booking_domain_consequence_replenishes_in_same_bounded_chain(owner_engine, registry, depth):
    demo = seed_demo(owner_engine)
    ctx = owner_for(demo, owner_engine)
    root, item = uuid4(), demo.items["Cable set"]
    ai = origin(ctx, "front_desk", "intake", root, depth)
    autonomy_rule(demo, owner_engine, live=True)
    with transaction(ctx) as db:
        service_suppliers.create(
            db,
            ctx,
            SupplierIn(
                name="Example Cable Backup",
                email="cables@example.com",
                items=[SupplierItemIn(catalog_item_id=item, last_price_minor=1400, lead_time_days=3)],
            ),
        )
        available = inventory.get_availability(db, ctx, [item])[0].available
        if available != 2:
            inventory.record_movement(
                db, ctx, inventory.MovementIn(item_id=item, quantity=Decimal(2) - available, reason="receipt")
            )
        slot = scheduling.find_slots(
            db,
            ctx,
            scheduling.SlotQuery(
                service_id=demo.items["Standard tune-up"],
                earliest=clock.now() + timedelta(days=1),
                latest=clock.now() + timedelta(days=7),
                limit=1,
            ),
        )[0]
    with owner_engine.begin() as db:
        db.execute(text("update event_deliveries set status='done',done_at=now()"))
        db.execute(text("update jobs set run_at=:later"), {"later": clock.now() + timedelta(days=1)})
    with transaction(ctx) as db:
        p = service.create_proposal(
            db,
            ai,
            action_type="scheduling.book_appointment",
            title="Example tune-up",
            payload={
                "customer": {"id": str(demo.customers["Ada Example"])},
                "service_id": str(demo.items["Standard tune-up"]),
                "resource_id": str(slot.resource_id),
                "starts_at": slot.starts_at.isoformat(),
            },
        )
        proposal_id, revision_id = p.id, approve(db, ctx, p)
    assert engine.execute(demo.business, proposal_id, revision_id) == "succeeded"
    with transaction(ctx) as db:
        event = db.scalars(
            select(DomainEvent).where(
                DomainEvent.type == "inventory.stock_below_reorder_level", DomainEvent.correlation_id == root
            )
        ).one()
        assert event.agent_key == "front_desk" and event.causation_id == proposal_id
        event_id = event.id
    writer = FakeGateway([ReorderNote(explanation=NOTE).model_dump_json()])
    procurement = quote_script(demo)
    set_router_for_tests(fake_router_by_class(writer=writer, agent=procurement))
    events.deliver_pending(registry)
    for _ in range(2):
        job = jobs.claim("booking-replenishment", lane="ai")
        assert job is not None and job["kind"] in {"ai.reorder_note", "ai.procurement_request_quotes"}
        assert job["correlation_id"] == root and job["causation_id"] == event_id
        assert job["chain_depth"] == depth
        assert jobs.run_job(registry, job)
    with transaction(ctx) as db:
        runs = list(db.scalars(select(AiRun).where(AiRun.correlation_id == root, AiRun.id != ai.ai_run_id)))
        assert len(runs) == 2 and all(r.chain_depth == depth + 1 for r in runs)
        if depth == 6:
            assert all(r.error_code == "ai.chain_depth_exceeded" for r in runs)
            assert writer.calls == procurement.calls == []
            return
        assert all(r.status == "succeeded" for r in runs), [(r.status, r.error_code) for r in runs]
        proposals = list(
            db.scalars(
                select(ActionProposal).where(
                    ActionProposal.correlation_id == root,
                    ActionProposal.action_type.in_(
                        ("inventory.create_reorder_draft", "purchasing.send_quote_requests")
                    ),
                )
            )
        )
        assert {p.action_type for p in proposals} == {
            "inventory.create_reorder_draft",
            "purchasing.send_quote_requests",
        }
        reorder = next(p for p in proposals if p.action_type == "inventory.create_reorder_draft")
        reorder_id, reorder_revision = reorder.id, reorder.current_revision_id
    assert engine.execute(demo.business, reorder_id, reorder_revision) == "succeeded"
    with transaction(ctx) as db:
        counter = db.scalars(select(ChainBudget).where(ChainBudget.correlation_id == root)).one()
        assert counter.model_calls == len(writer.calls) + len(procurement.calls) == 4
        assert counter.actions == 2 and counter.cost_micros <= 500000
        assert db.scalar(select(Job.id).where(Job.correlation_id == root, Job.kind == "ai.procurement_request_quotes"))
    assert "inventory" not in AGENTS["front_desk"].handoffs
    assert "purchasing" not in AGENTS["front_desk"].handoffs


@pytest.mark.parametrize("condition", ["stopped", "depth", "allowed"])
def test_http_quote_extraction_resumes_request_workflow(shop, condition):
    ctx, root = shop["ctx"], uuid4()
    depth = 6 if condition == "depth" else 2
    ai = origin(ctx, "purchasing", "procurement", root, depth)
    with transaction(ctx) as db:
        p = service.create_proposal(
            db,
            ai,
            action_type="purchasing.send_quote_requests",
            title="Example request",
            payload={
                "lines": [{"item_id": shop["item"], "quantity": "5"}],
                "supplier_ids": [shop["supplier"]],
                "needed_by": str(clock.now().date() + timedelta(days=7)),
                "reply_by": str(clock.now().date() + timedelta(days=2)),
            },
        )
        producer_id, revision_id = p.id, approve(db, ctx, p)
    assert engine.execute(shop["business"], producer_id, revision_id) == "succeeded"
    with transaction(ctx) as db:
        attempt = db.scalars(select(ExecutionAttempt).where(ExecutionAttempt.proposal_id == producer_id)).one()
        request_id = UUID(attempt.result["quote_request_id"])
        if condition == "stopped":
            chain = db.scalars(select(ChainBudget).where(ChainBudget.correlation_id == root)).one()
            chain.stopped_reason, chain.model_calls = "ai.chain_call_limit", 30
    gateway = FakeGateway(
        [
            json.dumps(
                {"lines": [{"item_id": shop["item"], "unit_price": "11.40"}], "currency": "USD", "lead_time_days": 3}
            )
        ]
    )
    set_router_for_tests(fake_router_by_class(fast_extract=gateway))
    response = shop["client"].post(
        f"/api/v1/ai/procurement/quote-requests/{request_id}/extract",
        json={"supplier_id": shop["supplier"], "text": "Unit price USD 11.40. Delivery 3 days."},
    )
    if condition != "allowed":
        reason = "ai.chain_call_limit" if condition == "stopped" else "ai.chain_depth_exceeded"
        assert response.status_code == (429 if condition == "stopped" else 409), response.text
        assert response.json()["code"] == reason and gateway.calls == []
    else:
        assert response.status_code == 200, response.text
    with transaction(ctx) as db:
        runs = list(db.scalars(select(AiRun).where(AiRun.prompt_name == "supplier_quote_extract")))
        assert len(runs) == 1 and runs[0].correlation_id == root
        assert runs[0].causation_id == producer_id and runs[0].chain_depth == depth + 1
        assert runs[0].status == ("succeeded" if condition == "allowed" else "policy_blocked")
        assert len(list(db.scalars(select(ChainBudget)))) == 1


def test_disabled_policy_fallback_human_revision_resumes_after_crash(owner_engine, registry, monkeypatch):
    demo = seed_demo(owner_engine)
    ctx = owner_for(demo, owner_engine)
    autonomy_rule(demo, owner_engine, live=True)
    proposal_id = reorder_note.run(
        demo.items["Cable set"],
        demo.business,
        router=fake_router_by_class(writer=FakeGateway([ReorderNote(explanation=NOTE).model_dump_json()])),
    )
    with transaction(ctx) as db:
        p = db.get(ActionProposal, proposal_id)
        old_revision = p.current_revision_id
        patch_settings(db, ctx, "inventory", AgentPatch(version=0, enabled=False))
    assert engine.execute(demo.business, proposal_id, old_revision) == "pending_review"
    with transaction(ctx) as db:
        p = db.get(ActionProposal, proposal_id)
        current_revision = approve(db, ctx, p)
        root = p.correlation_id
    original = engine.executor_for

    def crashing_executor(action_type):
        def crash(*args):
            raise RuntimeError("simulated worker crash after TX-1")

        return replace(original(action_type), run=crash)

    monkeypatch.setattr(engine, "executor_for", crashing_executor)
    with pytest.raises(RuntimeError, match="simulated worker crash"):
        engine.execute(demo.business, proposal_id, current_revision)
    monkeypatch.setattr(engine, "executor_for", original)
    with transaction(ctx) as db:
        attempts = list(db.scalars(select(ExecutionAttempt).where(ExecutionAttempt.proposal_id == proposal_id)))
        assert {a.revision_id: a.status for a in attempts} == {old_revision: "failed", current_revision: "running"}
        assert db.get(ActionProposal, proposal_id).status == "executing"
    assert engine._claim(demo.business, proposal_id, old_revision) == "done"
    assert engine.execute(demo.business, proposal_id, current_revision) == "succeeded"
    assert engine.execute(demo.business, proposal_id, current_revision) == "done"
    with transaction(ctx) as db:
        assert db.get(ActionProposal, proposal_id).status == "succeeded"
        assert db.scalar(select(ChainBudget.actions).where(ChainBudget.correlation_id == root)) == 1
