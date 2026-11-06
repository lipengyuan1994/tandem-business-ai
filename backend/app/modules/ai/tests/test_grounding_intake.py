"""Run-scoped facts and independent checker accounting through the real intake path."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import Engine, text

from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.types import ChatRequest
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.tests.tool_helpers import seed_demo
from app.modules.ai.usecases import intake


def test_repeated_db_tools_keep_both_returned_observations_and_account_checker(owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    conv, msg = inbound(demo.business, body="Brake pads or cables?")
    agent = FakeGateway(
        [
            step_tool("check_stock", item_ids=[demo.items["Brake pads (pair)"]]),
            step_tool("check_stock", item_ids=[demo.items["Cable set"]]),
            step_tool("propose_reply", conversation_id=conv, text="Which part would you like?"),
            step_final("Question prepared"),
        ]
    )

    def check(request: ChatRequest) -> str:
        facts = json.loads(request.messages[1].content.split("## db_tools\n")[1])
        observations = facts["check_stock"]
        assert len(observations) == 2
        assert observations[0]["items"][0]["item_name"] == "Brake pads (pair)"
        assert observations[1]["items"][0]["item_name"] == "Cable set"
        assert request.model == "fake-grounding"
        return '{"claims":[],"verdict":"supported"}'

    checker = FakeGateway(check)
    result = intake.run(
        msg,
        demo.business,
        router=fake_router_by_class(
            agent=agent, grounding=checker, safety=FakeGateway(['{"flagged":false,"category":"none"}'])
        ),
    )
    assert len(result.proposal_ids) == 1 and len(checker.calls) == 1
    with owner_engine.connect() as db:
        assert set(db.scalars(text("select model_class from ai_runs where business_id=:b"), {"b": demo.business})) == {
            "agent",
            "safety",
            "grounding",
        }
        assert db.scalar(text("select input_tokens from ai_usage_daily where business_id=:b"), {"b": demo.business}) > 0


def test_checker_cannot_authorize_effect_after_parent_intake_timeout(
    owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.ai.agent import runner

    demo = seed_demo(owner_engine)
    conv, msg = inbound(demo.business)
    elapsed = [0.0]
    monkeypatch.setattr(runner, "_monotonic", lambda: elapsed[0])

    def generate(_: ChatRequest) -> str:
        elapsed[0] = 80.0
        return step_tool("propose_reply", conversation_id=conv, text="Which service would you like?")

    def check(_: ChatRequest) -> str:
        elapsed[0] = 91.0  # checker fits its own deadline, but the generating intake no longer does
        return '{"claims":[],"verdict":"supported"}'

    result = intake.run(
        msg,
        demo.business,
        router=fake_router_by_class(
            agent=FakeGateway(generate),
            grounding=FakeGateway(check),
            safety=FakeGateway(['{"flagged":false,"category":"none"}']),
        ),
    )
    assert result.status == "failed" and result.error_code == "ai.run_timeout" and not result.proposal_ids
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from action_proposals where conversation_id=:c"), {"c": conv}) == 0
