"""End-to-end causality, durable supplier waits and honest completion evidence."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.main import create_app
from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import RunSpec
from app.modules.ai.agent.runner import run_agent
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.models import AiRun
from app.modules.ai.team_models import ChainBudget
from app.modules.ai.tests.helpers import fake_router_by_class, insert_run, step_final
from app.modules.ai.tests.test_reorder_note import NOTE, autonomy_rule
from app.modules.ai.tests.tool_helpers import seed_demo
from app.modules.approvals import engine, service
from app.modules.approvals.models import ActionProposal, ExecutionAttempt, ProposalRevision
from app.modules.identity.testing import login_as
from app.modules.inventory import public as inventory
from app.modules.notifications import service as notifications
from app.modules.notifications.adapters import ADAPTERS, RetryableError
from app.modules.notifications.models import OutboxMessage
from app.modules.purchasing import quote_service
from app.platform import clock, events, jobs
from app.platform.context import assistant_context, system_context
from app.platform.db import transaction
from app.platform.models import DomainEvent, Job
from tests.integration.test_a1_chat_confirm import confirm, order, proposal
from tests.integration.test_a1_chat_confirm import shop as shop
from tests.integration.test_ai_team import configured as configured
from tests.integration.test_ai_team import owner_for


def test_event_job_run_proposal_execution_emitted_event_chain(owner_engine, registry):
    demo = seed_demo(owner_engine)
    owner = owner_for(demo, owner_engine)
    autonomy_rule(demo, owner_engine, live=True)
    with owner_engine.begin() as db:
        db.execute(text("update event_deliveries set status='done',done_at=now()"))
        db.execute(text("update jobs set run_at=:later"), {"later": clock.now() + timedelta(days=1)})
    gateway = FakeGateway([json.dumps({"explanation": NOTE})])
    set_router_for_tests(fake_router_by_class(writer=gateway))
    ctx, item = system_context(demo.business), demo.items["Cable set"]
    with transaction(ctx) as db:
        inventory.record_movement(db, ctx, inventory.MovementIn(item_id=item, quantity=Decimal(5), reason="receipt"))
        inventory.record_movement(
            db, ctx, inventory.MovementIn(item_id=item, quantity=Decimal(-5), reason="consumption")
        )
    events.deliver_pending(registry)
    job = jobs.claim("team-chain", lane="ai")
    assert job is not None and job["kind"] == "ai.reorder_note"
    root = job["correlation_id"]
    assert root is not None and job["causation_id"] == root
    assert jobs.run_job(registry, job)
    with transaction(ctx) as db:
        run = db.scalars(select(AiRun).where(AiRun.use_case == "reorder_note")).one()
        p = db.scalars(select(ActionProposal).where(ActionProposal.ai_run_id == run.id)).one()
        assert run.correlation_id == p.correlation_id == root
        assert run.causation_id == job["id"] and p.causation_id == run.id
        assert run.agent_key == p.agent_key == "inventory"
        pid, revision = p.id, p.current_revision_id
        execution_job = db.scalars(select(Job).where(Job.kind == "approvals.execute")).one()
        assert execution_job.correlation_id == root and execution_job.causation_id == pid
    assert engine.execute(demo.business, pid, revision) == "succeeded"
    with transaction(ctx) as db:
        completed = db.scalars(
            select(DomainEvent).where(DomainEvent.type == "approvals.proposal_closed", DomainEvent.causation_id == pid)
        ).one()
        assert completed.correlation_id == root and completed.agent_key == "inventory"
        assert db.scalar(select(ChainBudget.actions).where(ChainBudget.correlation_id == root)) == 1
    client = login_as(create_app(registry), demo.business, ["owner"], user_id=owner.user_id)
    chain = client.get(f"/api/v1/ai/chains/{root}").json()
    positions = {UUID(s["id"]): i for i, s in enumerate(chain["steps"])}
    assert positions[root] < positions[job["id"]] < positions[run.id] < positions[pid] < positions[completed.id]
    activity = client.get("/api/v1/ai/agents/inventory/activity?limit=200").json()
    kinds = {i["kind"] for i in activity["items"]}
    assert {"run", "proposal", "decision", "execution", "autonomy"} <= kinds
    executed = next(i for i in activity["items"] if i["kind"] == "execution")
    assert executed["result_link"].startswith("/api/v1/inventory/reorder-drafts/")
    team = client.get("/api/v1/ai/agents").json()["items"]
    inventory_agent = next(a for a in team if a["key"] == "inventory")
    rule = next(r for r in inventory_agent["autonomy"] if r["action_type"] == "inventory.create_reorder_draft")
    assert rule["summary"]["acted"] == 1
    paged, cursor = [], None
    for _ in range(20):
        page = client.get(
            "/api/v1/ai/agents/inventory/activity", params={"limit": 1, **({"cursor": cursor} if cursor else {})}
        ).json()
        paged.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert [i["id"] for i in paged] == [i["id"] for i in activity["items"]]
    reset_router_cache()


def make_ai_proposal(shop, root, *, fail=False):
    ctx = shop["ctx"]
    run_id = insert_run(ctx, use_case="copilot", agent_key="operations_manager", correlation_id=root)
    ai = replace(
        assistant_context(
            ctx.business_id, frozenset({"ai.assistant.use"}), on_behalf_of_user_id=None, ai_run_id=run_id
        ),
        correlation_id=root,
        agent_key="operations_manager",
        chain_depth=1,
    )
    with transaction(ctx) as db:
        p = service.create_proposal(
            db,
            ai,
            action_type="testing.noop_write",
            title="Example internal action",
            payload={"name": "Example item", "write_test_item": True, "fail": fail},
        )
        rev = db.get(ProposalRevision, p.current_revision_id)
        service.decide(
            db, ctx, p.id, decision="approve", revision_id=rev.id, payload_hash=rev.payload_hash, comment=None
        )
        return p.id, rev.id


def test_human_approved_ai_actions_obey_chain_cap_and_failed_effects_rollback(shop, configured):
    root = uuid4()
    with transaction(shop["ctx"]) as db:
        db.add(ChainBudget(business_id=shop["business"], correlation_id=root, actions=9))
    p, r = make_ai_proposal(shop, root, fail=True)
    assert engine.execute(shop["business"], p, r) == "failed"
    with transaction(shop["ctx"]) as db:
        assert db.scalar(select(ChainBudget.actions)) == 9
        failure_event = db.scalars(select(DomainEvent).where(DomainEvent.type == "approvals.proposal_closed")).one()
        assert failure_event.correlation_id == root and failure_event.causation_id == p
        assert db.scalar(text("select count(*) from test_items")) == 0
    p, r = make_ai_proposal(shop, root)
    assert engine.execute(shop["business"], p, r) == "succeeded"
    p, r = make_ai_proposal(shop, root)
    assert engine.execute(shop["business"], p, r) == "failed"
    with transaction(shop["ctx"]) as db:
        assert db.get(ActionProposal, p).failure_code == "ai.chain_action_limit"
        assert db.scalar(select(ChainBudget.actions)) == 10
        assert db.scalar(text("select count(*) from test_items")) == 1


def test_inventory_to_purchasing_allowed_and_scoped_factories_isolated(shop, configured):
    parent = replace(system_context(shop["business"]), correlation_id=uuid4(), agent_key="inventory", chain_depth=1)
    factory = background_ctx_factory(shop["business"], use_case="procurement", parent=parent)
    result = run_agent(
        factory,
        RunSpec(
            use_case="procurement",
            prompt=load_prompt("procurement_request"),
            task="Review supplied stock",
            tools=[],
            context_blocks=[],
            trigger_type="job",
            trigger_key="allowed-edge",
        ),
        router=fake_router_by_class(agent=FakeGateway([step_final()])),
    )
    assert result.status == "succeeded"
    with transaction(parent) as db:
        row = db.get(AiRun, result.run_id)
        assert row.agent_key == "purchasing" and row.chain_depth == 2 and row.correlation_id == parent.correlation_id
    other = background_ctx_factory(shop["business"], use_case="procurement")(uuid4())
    assert other.correlation_id != parent.correlation_id and other.chain_depth == 1


def test_delivery_timeline_evidence_and_retry_jobs_keep_chain(shop, configured, monkeypatch):
    order(shop, sent=True)
    po = order(shop)
    p = proposal(shop, po)
    root = uuid4()
    with transaction(shop["ctx"]) as db:
        row = db.get(ActionProposal, p["id"])
        row.correlation_id, row.agent_key, row.chain_depth = root, "purchasing", 1
    assert confirm(shop, p).status_code == 200
    assert engine.execute(shop["business"], p["id"], p["revision_id"]) == "succeeded"
    with transaction(shop["ctx"]) as db:
        mail = db.scalars(select(OutboxMessage)).one()
        mid = mail.id
        mail.not_before = clock.now()
        original_job = db.scalars(select(Job).where(Job.kind == "notifications.deliver")).one()
        assert original_job.correlation_id == root

    class Retry:
        def send(self, message, key):
            return RetryableError("Example temporary failure")

    with transaction(shop["ctx"]) as db:
        channel = db.get(OutboxMessage, mid).channel
    monkeypatch.setitem(ADAPTERS, channel, Retry())
    assert notifications.deliver(shop["business"], mid) == "retry"
    with transaction(shop["ctx"]) as db:
        retries = list(db.scalars(select(Job).where(Job.kind == "notifications.deliver")))
        assert len(retries) == 2 and all(j.correlation_id == root for j in retries)
        mail = db.get(OutboxMessage, mid)
        mail.status = "delivered"
        notifications._status_event(db, system_context(shop["business"]), mail)
    activity = shop["client"].get("/api/v1/ai/agents/purchasing/activity").json()["items"]
    delivered = next(i for i in activity if i["kind"] == "delivery")
    assert delivered["delivery_state"] == "delivered" and delivered["correlation_id"] == str(root)
    assert shop["client"].get(delivered["result_link"]).status_code == 200
    assert "parts@example.com" not in str(activity)


def test_supplier_reply_rejoins_original_waiting_chain(shop, configured):
    ctx, root = shop["ctx"], uuid4()
    run_id = insert_run(ctx, use_case="procurement", agent_key="purchasing", correlation_id=root)
    ai = replace(
        assistant_context(
            ctx.business_id, frozenset({"ai.assistant.use"}), on_behalf_of_user_id=None, ai_run_id=run_id
        ),
        correlation_id=root,
        agent_key="purchasing",
        chain_depth=2,
    )
    with transaction(ctx) as db:
        p = service.create_proposal(
            db,
            ai,
            action_type="purchasing.send_quote_requests",
            title="Example supplier request",
            payload={
                "lines": [{"item_id": shop["item"], "quantity": "5"}],
                "supplier_ids": [shop["supplier"]],
                "needed_by": str(clock.now().date() + timedelta(days=7)),
                "reply_by": str(clock.now().date() + timedelta(days=2)),
            },
        )
        revision = db.get(ProposalRevision, p.current_revision_id)
        service.decide(
            db, ctx, p.id, decision="approve", revision_id=revision.id, payload_hash=revision.payload_hash, comment=None
        )
        pid, rid = p.id, revision.id
    assert engine.execute(shop["business"], pid, rid) == "succeeded"
    with transaction(ctx) as db:
        attempt = db.scalars(select(ExecutionAttempt).where(ExecutionAttempt.proposal_id == pid)).one()
        qr = UUID(attempt.result["quote_request_id"])
        visitor = replace(
            ctx,
            actor_kind="visitor",
            user_id=None,
            permissions=frozenset(),
            correlation_id=None,
            agent_key=None,
            chain_depth=0,
        )
        quote_service.answered_event(db, visitor, qr, UUID(shop["supplier"]), declined=True)
        event = db.scalars(select(DomainEvent).where(DomainEvent.type == "purchasing.quote_received")).one()
        assert event.correlation_id == root and event.causation_id == pid and event.agent_key == "purchasing"
        # Hourly discovery must reuse the same root even before the response subscriber runs.
        from app.modules.ai.chains import subject_context

        scanned = subject_context(db, system_context(ctx.business_id), "quote_request_id", qr)
        assert scanned.correlation_id == root and scanned.causation_id == pid


def test_payment_scan_uses_independent_invoice_chains(owner_engine, registry):
    from app.modules.ai.tests.test_batch_c_payment import second_invoice
    from app.modules.ai.usecases import payment_followup

    demo = seed_demo(owner_engine)
    second_invoice(owner_engine, demo.business)
    payment_followup.scan(demo.business)
    payment_followup.scan(demo.business)
    with transaction(system_context(demo.business)) as db:
        queued = list(db.scalars(select(Job).where(Job.kind == "ai.payment_followup")))
        assert len(queued) == 2 and len({j.correlation_id for j in queued}) == 2
