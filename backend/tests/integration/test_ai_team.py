"""P10-B1 API, workflow and execution boundaries; only fake models and synthetic fixtures."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import func, select, text

from app.modules.ai import lifecycle, subscribers
from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import ContextBlock
from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.models import AiRun
from app.modules.ai.schemas import ReorderNote
from app.modules.ai.structured import complete
from app.modules.ai.team_budget import reserve_model
from app.modules.ai.team_schemas import AgentPatch
from app.modules.ai.team_settings import patch_settings
from app.modules.ai.tests.helpers import fake_router_by_class, step_final
from app.modules.ai.tests.test_reorder_note import NOTE, autonomy_rule
from app.modules.ai.tests.tool_helpers import Demo, assistant, seed_demo
from app.modules.ai.tools.registry import execute_tool
from app.modules.ai.usecases import copilot, reorder_note
from app.modules.approvals import engine
from app.modules.approvals import service as approvals
from app.modules.approvals.models import ActionProposal, ProposalRevision
from app.modules.identity import public as identity
from app.modules.identity.testing import login_as
from app.modules.org import public as org
from app.platform import clock
from app.platform.config import get_settings
from app.platform.context import Context, system_context
from app.platform.db import transaction
from app.platform.models import AuditEvent, Job
from app.platform.testing.auth import FakeSessions
from tests.integration.test_a1_chat_confirm import shop as shop


@pytest.fixture(autouse=True)
def configured():
    reset_router_cache()
    gateway = FakeGateway([step_final()])
    set_router_for_tests(
        fake_router_by_class(agent=gateway, writer=gateway, fast_extract=gateway, safety=gateway, grounding=gateway)
    )
    yield gateway
    reset_router_cache()


@pytest.fixture
def demo(owner_engine, registry) -> Demo:
    return seed_demo(owner_engine)


def owner_for(demo: Demo, owner_engine) -> Context:
    with owner_engine.connect() as db:
        grantor = db.scalar(
            text("select granted_by from autonomy_rules where business_id=:b limit 1"), {"b": demo.business}
        )
    return Context("team-test", "user", grantor, demo.business, identity.ROLES["owner"])


def test_team_api_settings_lock_audit_defaults_and_permissions(shop, registry):
    client = shop["client"]
    result = client.get("/api/v1/ai/agents")
    assert result.status_code == 200, result.text
    rows = {r["key"]: r for r in result.json()["items"]}
    assert len(rows) == 7
    assert all(r["enabled"] == (k != "customer_care") for k, r in rows.items())
    assert rows["inventory"]["status"] == "on"
    assert rows["inventory"]["waiting"] == {"count": 0, "oldest_at": None}
    assert set(rows["finance"]["today"]) == {
        "runs",
        "proposals_created",
        "actions_executed_by_people",
        "actions_executed_by_rules",
        "handed_to_person",
    }
    saved = client.patch(
        "/api/v1/ai/agents/inventory", json={"version": 0, "enabled": False, "daily_cost_limit_usd": "0.010000"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1 and saved.json()["status"] == "off"
    assert saved.json()["updated_by"] == str(shop["ctx"].user_id)
    assert client.patch("/api/v1/ai/agents/inventory", json={"version": 0, "enabled": True}).status_code == 409
    saved = client.patch("/api/v1/ai/agents/inventory", json={"version": 1, "daily_cost_limit_usd": None})
    assert saved.status_code == 200 and saved.json()["daily_cost_limit_usd"] is None
    assert client.patch("/api/v1/ai/agents/unknown", json={"version": 0, "enabled": False}).status_code == 404
    assert client.patch("/api/v1/ai/agents/inventory", json={"version": 2, "enabled": None}).status_code == 422
    with transaction(shop["ctx"]) as db:
        audits = list(db.scalars(select(AuditEvent).where(AuditEvent.action == "ai.agent.settings_changed")))
        assert len(audits) == 2 and audits[0].actor_id == shop["ctx"].user_id
    reader = login_as(shop["app"], shop["business"], ["operator"])
    assert reader.get("/api/v1/ai/agents").status_code == 200
    assert reader.patch("/api/v1/ai/agents/inventory", json={"version": 2, "enabled": True}).status_code == 403


def test_read_and_write_permissions_are_independent(shop, registry):
    # A session with exactly the documented write permission can PATCH without Activity permission.
    fake = FakeSessions()
    token, csrf = fake.add(shop["business"], ["org.settings.write"], shop["ctx"].user_id)
    registry.session_resolver = fake
    client = shop["client"]
    client.cookies.clear()
    from app.platform.permissions import SESSION_COOKIE

    client.cookies.set(SESSION_COOKIE, token)
    client.headers["X-CSRF-Token"] = csrf
    assert client.get("/api/v1/ai/agents").status_code == 403
    changed = client.patch("/api/v1/ai/agents/finance", json={"version": 0, "enabled": False})
    assert changed.status_code == 200, changed.text


def test_settings_concurrent_version_zero_only_one_wins(shop):
    def change(_):
        try:
            with transaction(shop["ctx"]) as db:
                patch_settings(db, shop["ctx"], "inventory", AgentPatch(version=0, enabled=False))
            return True
        except Exception as exc:
            assert getattr(exc, "code", None) == "ai.agent_version_conflict"
            return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(change, range(6))) == 1


@pytest.mark.parametrize(
    "key,kind,payload",
    [
        ("front_desk", "ai.intake", {"message_id": str(uuid.uuid4())}),
        ("inventory", "ai.reorder_note", {"item_id": str(uuid.uuid4())}),
        ("purchasing", "ai.procurement_scan", {}),
        ("finance", "ai.payment_followup_scan", {}),
    ],
)
def test_disabled_agent_skips_queued_jobs_and_triggers(shop, registry, configured, key, kind, payload):
    shop["client"].patch(f"/api/v1/ai/agents/{key}", json={"version": 0, "enabled": False})
    ctx = system_context(shop["business"])
    registry.jobs[kind].handler(ctx, payload)
    with transaction(ctx) as db:
        if key == "front_desk":
            subscribers.intake_trigger(db, ctx, {"author_kind": "visitor", **payload}, uuid.uuid4())
        elif key == "inventory":
            subscribers.reorder_trigger(db, ctx, payload, uuid.uuid4())
        elif key == "purchasing":
            subscribers.compare_trigger(
                db, ctx, {"all_answered": True, "quote_request_id": str(uuid.uuid4())}, uuid.uuid4()
            )
        assert db.scalar(select(func.count()).select_from(AiRun)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == kind)) == 0
    assert not configured.calls


def test_disabled_inventory_returns_policy_action_to_human_and_human_can_execute(demo, owner_engine, registry):
    autonomy_rule(demo, owner_engine, live=True)
    p = reorder_note.run(
        demo.items["Cable set"],
        demo.business,
        router=fake_router_by_class(writer=FakeGateway([ReorderNote(explanation=NOTE).model_dump_json()])),
    )
    ctx = owner_for(demo, owner_engine)
    with transaction(ctx) as db:
        row = db.get(ActionProposal, p)
        old_revision = row.current_revision_id
        patch_settings(db, ctx, "inventory", AgentPatch(version=0, enabled=False))
    assert engine.execute(demo.business, p, old_revision) == "pending_review"
    with transaction(ctx) as db:
        row = db.get(ActionProposal, p)
        assert row.status == "pending_review" and row.failure_code == "ai.agent_disabled"
        assert row.current_revision_id != old_revision
        rev = db.get(ProposalRevision, row.current_revision_id)
        approvals.decide(
            db, ctx, p, decision="approve", revision_id=rev.id, payload_hash=rev.payload_hash, comment=None
        )
        fresh_revision = rev.id
    assert engine.execute(demo.business, p, fresh_revision) == "succeeded"
    assert engine.execute(demo.business, p, old_revision) == "done"
    from app.main import create_app

    client = login_as(
        create_app(registry),
        demo.business,
        ["owner"],
        user_id=ctx.user_id,
    )
    assert any("ai.agent_disabled" in i["reasons"] for i in client.get("/api/v1/ai/exceptions").json()["items"])


def test_copilot_off_and_target_scope_and_disable_after_prepare(shop, configured):
    client, tid = shop["client"], shop["thread"]
    prepared = copilot.prepare_message(
        shop["ctx"], uuid.UUID(tid), copilot.MessageCreate(content="Check stock", agent="inventory")
    )
    assert set(prepared.spec.tools) <= {
        "check_stock",
        "list_low_stock",
        "get_service_parts",
        "list_appointments",
        "propose_reorder",
    }
    assert "Inventory" in prepared.spec.task and "supplier" not in prepared.spec.task
    client.patch("/api/v1/ai/agents/operations_manager", json={"version": 0, "enabled": False})
    emitted = []
    copilot.run_message(prepared, emitted.append, threading.Event())
    assert emitted[-1].error_code == "ai.agent_disabled" and not configured.calls
    with transaction(shop["ctx"]) as db:
        assert db.scalar(select(func.count()).select_from(AiRun)) == 0
    denied = client.post(f"/api/v1/ai/copilot/threads/{tid}/messages", json={"content": "Hello"})
    assert denied.status_code == 409 and "Operations Manager is off" in denied.json()["detail"]
    client.patch("/api/v1/ai/agents/operations_manager", json={"version": 1, "enabled": True})
    client.patch("/api/v1/ai/agents/inventory", json={"version": 0, "enabled": False})
    denied = client.post(
        f"/api/v1/ai/copilot/threads/{tid}/messages", json={"content": "Check stock", "agent": "inventory"}
    )
    assert denied.status_code == 409 and "Inventory is off" in denied.json()["detail"]


def test_tool_checks_disabled_agent_after_model_before_direct_hold(demo, owner_engine):
    ctx, state = assistant(demo.business)
    with transaction(ctx) as db:
        db.get(AiRun, ctx.ai_run_id).agent_key = "inventory"
    owner = owner_for(demo, owner_engine)
    with transaction(owner) as db:
        patch_settings(db, owner, "inventory", AgentPatch(version=0, enabled=False))
    result = execute_tool(ctx, "list_low_stock", {}, ["list_low_stock"], state)
    assert result["error"] == "ai.agent_disabled"
    result = execute_tool(
        ctx,
        "hold_slot",
        {
            "service_id": str(uuid.uuid4()),
            "resource_id": str(uuid.uuid4()),
            "customer_id": str(uuid.uuid4()),
            "starts_at": clock.now().isoformat(),
        },
        ["hold_slot"],
        state,
    )
    assert result["error"] == "ai.agent_disabled"


@pytest.mark.parametrize(
    "source,depth,reason",
    [("purchasing", 1, "ai.chain_transition_blocked"), ("inventory", 6, "ai.chain_depth_exceeded")],
)
def test_graph_and_depth_block_before_model_and_show_exception(shop, source, depth, reason):
    parent = replace(system_context(shop["business"]), correlation_id=uuid.uuid4(), agent_key=source, chain_depth=depth)
    factory = background_ctx_factory(shop["business"], use_case="reorder_note", parent=parent)
    gw = FakeGateway([])
    with pytest.raises(AiError) as err:
        complete(
            factory,
            use_case="reorder_note",
            prompt=load_prompt("reorder_note"),
            facts=[ContextBlock(label="facts", text="test", untrusted=True)],
            output_model=ReorderNote,
            trigger_type="job",
            trigger_key=str(uuid.uuid4()),
            router=fake_router_by_class(writer=gw),
        )
    assert err.value.code == reason and not gw.calls
    chain = shop["client"].get(f"/api/v1/ai/chains/{parent.correlation_id}")
    assert chain.status_code == 200
    assert chain.json()["steps"][-1]["reasons"] == [reason]
    assert shop["client"].get("/api/v1/ai/exceptions").json()["items"][0]["reasons"] == [reason]


def test_regulated_availability_uses_per_class_and_global_pause_unchanged(shop, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    rows = {r["key"]: r for r in shop["client"].get("/api/v1/ai/agents").json()["items"]}
    assert rows["inventory"]["status"] == "unavailable" and "local route for writer" in rows["inventory"]["reason"]
    monkeypatch.setattr(get_settings(), "data_profile", "standard")
    with transaction(shop["ctx"]) as db:
        org.set_autonomy_paused(db, shop["ctx"], True)
    assert (
        next(r for r in shop["client"].get("/api/v1/ai/agents").json()["items"] if r["key"] == "inventory")["status"]
        == "paused"
    )
    prepared = copilot.prepare_message(shop["ctx"], uuid.UUID(shop["thread"]), copilot.MessageCreate(content="Hello"))
    copilot.release_generation(prepared)


def test_chain_foreign_tenant_and_export_metadata(shop, registry):
    with transaction(shop["ctx"]) as db:
        patch_settings(db, shop["ctx"], "finance", AgentPatch(version=0, enabled=False))
        ctx = replace(shop["ctx"], correlation_id=uuid.uuid4(), agent_key="finance")
        reserve_model(db, replace(ctx, agent_key="inventory"), 12)
        exported = lifecycle.export(db, ctx)
        assert exported["ai_agent_settings"][0]["agent_key"] == "finance"
        assert exported["ai_chain_budgets"][0]["correlation_id"] == str(ctx.correlation_id)
        assert {"correlation_id", "causation_id", "agent_key", "chain_depth"} <= set(AiRun.__table__.columns.keys())
    from app.modules.org.testing import make_business

    other = login_as(shop["app"], make_business(slug="other-team-example"), ["owner"])
    assert other.get(f"/api/v1/ai/chains/{ctx.correlation_id}").status_code == 404


def test_untargeted_copilot_retains_legacy_operation_body_hash(shop):
    thread_id = uuid.UUID(shop["thread"])
    with transaction(shop["ctx"]) as db:
        copilot.claim_operation(db, shop["ctx"], "copilot", thread_id, "legacy-operation", {"content": "Hello"})
    prepared = copilot.prepare_message(
        shop["ctx"], thread_id, copilot.MessageCreate(content="Hello"), idempotency_key="legacy-operation"
    )
    copilot.release_generation(prepared)
