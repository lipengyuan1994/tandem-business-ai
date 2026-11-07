"""P10-B1 deterministic controls; real isolated PostgreSQL, synthetic data only."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from app.modules.ai.agents import ACTION_OWNERS, AGENTS
from app.modules.ai.errors import AiError
from app.modules.ai.team_budget import reserve_model
from app.modules.ai.team_models import ChainBudget
from app.modules.ai.team_schemas import AgentPatch
from app.modules.ai.team_settings import enabled
from app.platform.context import system_context
from app.platform.db import transaction


def test_registry_consistent(registry):
    from app.modules.approvals import public as approvals

    actions = [a for agent in AGENTS.values() for a in agent.action_types]
    assert len(actions) == len(set(actions)) == len(ACTION_OWNERS)
    assert set(actions) == registry.executors.keys() - {"testing.noop_write"}
    assert approvals.RULE_ELIGIBLE_ACTIONS <= set(actions)
    assert all(set(agent.handoffs) <= AGENTS.keys() for agent in AGENTS.values())
    assert "purchasing" in AGENTS["inventory"].handoffs
    assert "marketing" not in AGENTS["purchasing"].handoffs
    assert set(AGENTS["customer_care"].triggers) == {
        "assistant_request",
        "scheduling.appointment_completed",
        "conversations.message_received",
        "ai.customer_care_classify",
        "ai.customer_care_checkin",
        "ai.customer_care_recall",
        "ai.customer_care_recall_subject",
        "ai.customer_care_triage",
    }
    assert AGENTS["customer_care"].action_types == ("customer_care.send_message",)
    assert not AGENTS["customer_care"].default_enabled


def test_upgrade_defaults_and_rls(business, other_business):
    ctx = system_context(business)
    with transaction(ctx) as db:
        assert all(enabled(db, ctx, key) == (key != "customer_care") for key in AGENTS)
        db.add(ChainBudget(business_id=other_business, correlation_id=uuid4()))
        with pytest.raises(ProgrammingError):
            db.flush()


def test_chain_model_reservation_race(business):
    ctx = replace(system_context(business), correlation_id=uuid4(), agent_key="inventory", chain_depth=1)

    def attempt(_):
        try:
            with transaction(ctx) as db:
                reserve_model(db, ctx, 0)
            return True
        except AiError as exc:
            assert exc.code == "ai.chain_call_limit"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(45))) == 30
    with transaction(ctx) as db:
        assert db.scalar(select(ChainBudget.model_calls)) == 30


def test_agent_daily_ceiling(business):
    ctx = replace(system_context(business), correlation_id=uuid4(), agent_key="inventory", chain_depth=1)
    with transaction(ctx) as db:
        # Setup uses an owner-like context; production writes additionally require an authenticated user.
        db.execute(
            text(
                "insert into ai_agent_settings(id,business_id,agent_key,enabled,daily_cost_limit_usd) "
                "values (:id,:b,'inventory',true,0.01)"
            ),
            {"id": uuid4(), "b": business},
        )
        reserve_model(db, ctx, 10000)
    with pytest.raises(AiError, match=r"ai\.agent_limit_reached"), transaction(ctx) as db:
        reserve_model(db, ctx, 1)


def test_patch_rejects_invalid_limits():
    for value in ("-1", "NaN", "Infinity", "0.0000001"):
        with pytest.raises(ValueError):
            AgentPatch(version=0, daily_cost_limit_usd=Decimal(value))
