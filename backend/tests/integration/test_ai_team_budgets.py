"""Reservation races, UTC reconciliation and admission after a daily limit."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.budget_models import CostPeriod
from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.routes import RouteEntry
from app.modules.ai.gateway.types import ChatMessage, ChatRequest, ChatResult, Usage
from app.modules.ai.models import AiRun, AiUsageDaily
from app.modules.ai.reservations import conservative_input, reconcile, reserve
from app.modules.ai.schemas import ReorderNote
from app.modules.ai.structured import complete
from app.modules.ai.team_budget import MAX_COST_MICROS, reserve_action, reserve_model
from app.modules.ai.team_models import AgentUsageDaily, ChainBudget
from app.modules.ai.team_schemas import AgentPatch
from app.modules.ai.team_settings import patch_settings
from app.modules.ai.tests.helpers import fake_router_by_class, insert_run
from app.modules.notifications.models import InAppNotification
from app.platform import clock
from app.platform.context import system_context
from app.platform.db import transaction
from tests.integration.test_a1_chat_confirm import shop as shop
from tests.integration.test_ai_team import configured as configured


def test_chain_cost_reservation_concurrent_race(shop, configured):
    ctx = replace(system_context(shop["business"]), agent_key="inventory", correlation_id=uuid4())

    def attempt(_):
        try:
            with transaction(ctx) as db:
                reserve_model(db, ctx, 20000)
            return True
        except AiError as exc:
            assert exc.code == "ai.chain_cost_limit"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(40))) == 25
    with transaction(ctx) as db:
        assert db.scalar(select(ChainBudget.cost_micros)) == MAX_COST_MICROS
        assert db.scalar(select(AgentUsageDaily.cost_micros)) == MAX_COST_MICROS


def test_agent_limit_serializes_different_chains(shop, configured):
    ctx = replace(system_context(shop["business"]), agent_key="inventory")
    with transaction(shop["ctx"]) as db:
        patch_settings(db, shop["ctx"], "inventory", AgentPatch(version=0, daily_cost_limit_usd=Decimal("0.01")))

    def attempt(_):
        fresh = replace(ctx, correlation_id=uuid4())
        try:
            with transaction(fresh) as db:
                reserve_model(db, fresh, 1000)
            return True
        except AiError as exc:
            assert exc.code == "ai.agent_limit_reached"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 10
    with transaction(ctx) as db:
        assert db.scalar(select(AgentUsageDaily.cost_micros)) == 10000
        assert db.scalar(select(func.count()).select_from(ChainBudget)) == 10


def test_exhausted_agent_admission_does_not_create_new_runs_and_notifies_once(shop, configured):
    with transaction(shop["ctx"]) as db:
        patch_settings(db, shop["ctx"], "inventory", AgentPatch(version=0, daily_cost_limit_usd=Decimal(0)))
    gateway = FakeGateway([])
    for _ in range(2):
        with pytest.raises(AiError, match=r"ai\.agent_limit_reached"):
            complete(
                background_ctx_factory(shop["business"], use_case="reorder_note"),
                use_case="reorder_note",
                prompt=load_prompt("reorder_note"),
                facts=[],
                output_model=ReorderNote,
                trigger_type="job",
                trigger_key=str(uuid4()),
                router=fake_router_by_class(writer=gateway),
            )
    with transaction(shop["ctx"]) as db:
        assert db.scalar(select(func.count()).select_from(AiRun)) == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(InAppNotification)
                .where(InAppNotification.kind == "ai.agent_limit_reached")
            )
            == 1
        )
    assert not gateway.calls


def test_agent_and_chain_cost_reconcile_to_original_day_after_utc_rollover(shop, configured):
    ctx = replace(system_context(shop["business"]), agent_key="inventory", correlation_id=uuid4())
    req = ChatRequest(
        messages=[ChatMessage(role="user", content="Example stock")], max_output_tokens=100, use_case="reorder_note"
    )
    entry = RouteEntry(provider="fake", model="priced", price_in_per_m=1, price_out_per_m=2)
    held = reserve(ctx, entry, req)
    assert held.cost == conservative_input(req) + 200
    next_day = clock.now() + timedelta(days=1)
    clock.set_clock(lambda: next_day)
    reconcile(
        ctx,
        held,
        entry,
        ChatResult(
            text="unused", usage=Usage(input_tokens=10, output_tokens=5), provider="fake", model="priced", latency_ms=1
        ),
    )
    with transaction(ctx) as db:
        usage = db.scalars(select(AgentUsageDaily)).one()
        assert usage.day == held.day and usage.cost_micros == 20
        chain = db.scalars(select(ChainBudget)).one()
        assert chain.cost_micros == 20 and chain.model_calls == 1


def test_regulated_agent_unavailable_when_only_local_writer_exists(shop, configured, monkeypatch):
    from app.modules.ai.gateway.router import Router, set_router_for_tests
    from app.modules.ai.gateway.routes import ProviderCfg, RoutesFile
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    route = RoutesFile(
        providers={
            "clinic": ProviderCfg(
                kind="openai_compat", vendor="local", hosted=False, base_url="http://clinic.example.test:8080/v1"
            )
        },
        routes={"writer": [RouteEntry(provider="clinic", model="local")]},
    )
    set_router_for_tests(Router(route, {"clinic": FakeGateway([])}, resolver=lambda _: ["10.1.2.3"]))
    rows = {r["key"]: r for r in shop["client"].get("/api/v1/ai/agents").json()["items"]}
    assert rows["inventory"]["status"] == "unavailable"
    assert rows["inventory"]["reason"] == "regulated: no local route for agent"


def test_chain_cost_denial_rolls_back_installation_and_stops_later_actions(shop):
    ctx = replace(system_context(shop["business"]), agent_key="inventory", correlation_id=uuid4(), chain_depth=1)
    with transaction(ctx) as db:
        db.add(ChainBudget(business_id=ctx.business_id, correlation_id=ctx.correlation_id, cost_micros=490000))
    from app.modules.ai.reservations import ReservedAdapter

    entry = RouteEntry(provider="fake", model="expensive", price_in_per_m=100)
    gw = FakeGateway([])
    req = ChatRequest(
        model="expensive",
        messages=[ChatMessage(role="user", content="test")],
        max_output_tokens=1,
        use_case="reorder_note",
    )
    with pytest.raises(AiError, match=r"ai\.chain_cost_limit"):
        ReservedAdapter(ctx, gw, [entry]).chat(req)
    with transaction(ctx) as db:
        assert db.scalar(select(func.count()).select_from(CostPeriod)) == 0
        assert db.scalar(select(func.count()).select_from(AiUsageDaily)) == 0
        assert db.scalar(select(ChainBudget.stopped_reason)) == "ai.chain_cost_limit"
    with pytest.raises(AiError, match=r"ai\.chain_cost_limit"), transaction(ctx) as db:
        reserve_action(db, ctx)
    assert not gw.calls


def test_chain_action_concurrent_race_and_rollback(shop):
    ctx = replace(system_context(shop["business"]), agent_key="inventory", correlation_id=uuid4())

    def attempt(_):
        try:
            with transaction(ctx) as db:
                reserve_action(db, ctx)
            return True
        except AiError as exc:
            assert exc.code == "ai.chain_action_limit"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 10
    with transaction(ctx) as db:
        assert db.scalar(select(ChainBudget.actions)) == 10
    fresh = replace(ctx, correlation_id=uuid4())
    with pytest.raises(ValueError), transaction(fresh) as db:
        reserve_action(db, fresh)
        raise ValueError("executor rolls back")
    with transaction(ctx) as db:
        assert db.scalar(select(func.count()).select_from(ChainBudget)) == 1


def test_agent_legacy_daily_spend_and_new_day_reset(shop):
    ctx = replace(system_context(shop["business"]), agent_key="inventory", correlation_id=uuid4())
    insert_run(ctx, use_case="reorder_note", cost_micros=9000)
    with transaction(shop["ctx"]) as db:
        patch_settings(db, shop["ctx"], "inventory", AgentPatch(version=0, daily_cost_limit_usd=Decimal("0.01")))
        reserve_model(db, ctx, 1000)
    with pytest.raises(AiError, match=r"ai\.agent_limit_reached"), transaction(ctx) as db:
        reserve_model(db, ctx, 1)
    with transaction(ctx) as db:
        assert db.scalar(select(AgentUsageDaily.cost_micros)) == 10000
    assert (
        next(r for r in shop["client"].get("/api/v1/ai/agents").json()["items"] if r["key"] == "inventory")["status"]
        == "limit_reached"
    )
    tomorrow = clock.now() + timedelta(days=1)
    clock.set_clock(lambda: tomorrow)
    with transaction(ctx) as db:
        reserve_model(db, ctx, 1000)


def test_agent_limit_notifies_once_and_no_provider_call(shop):
    client = shop["client"]
    client.patch("/api/v1/ai/agents/inventory", json={"version": 0, "daily_cost_limit_usd": "0"})
    from app.modules.ai.reservations import ReservedAdapter

    ctx = replace(system_context(shop["business"]), agent_key="inventory", correlation_id=uuid4())
    gw = FakeGateway([])
    entry = RouteEntry(provider="fake", model="unused")
    req = ChatRequest(model="unused", messages=[], max_output_tokens=1, use_case="reorder_note")
    for _ in range(3):
        with pytest.raises(AiError, match=r"ai\.agent_limit_reached"):
            ReservedAdapter(ctx, gw, [entry]).chat(req)
    with transaction(shop["ctx"]) as db:
        assert db.scalar(select(AgentUsageDaily.notified_at)) is not None
        count = db.scalar(text("select count(*) from in_app_notifications where kind='ai.agent_limit_reached'"))
        assert count == 1
    assert not gw.calls
