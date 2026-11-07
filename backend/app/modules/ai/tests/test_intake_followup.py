"""Final-step and checker-shape regressions from the second supervisor intake run."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.types import ChatRequest
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.tests.test_intake_agent import observation
from app.modules.ai.tests.tool_helpers import assistant, call, ok, seed_demo
from app.modules.ai.usecases import intake
from evals.runner import load_cases, run_case
from evals.runtime import LiveRuntime


def test_chain_stock_can_lookup_a_product_absent_from_preloaded_parts(owner_engine: Engine) -> None:
    case = next(c for c in load_cases("intake") if c.id == "intake-chain-stock")
    turn = 0
    body = ""

    def generate(request: ChatRequest) -> str:
        nonlocal turn, body
        turn += 1
        if turn == 1:
            # The preloaded service parts do not include this product/id. The model can use its name.
            assert "Chain 11-speed" not in request.messages[1].content.split('source="inbound_message"')[0]
            return step_tool("check_stock", query="Chain 11-speed")
        if turn == 2:
            row = observation(request)["data"]["items"][0]
            body = f"{row['item_name']}: {row['available']} available."
            with owner_engine.connect() as db:
                conv = db.scalar(text("select id from conversations where subject=:s"), {"s": case.id})
            return step_tool("propose_reply", conversation_id=conv, text=body)
        return step_final("Prepared the current stock answer.", needs_human=False)

    def check(_: ChatRequest) -> str:
        return json.dumps(
            {"claims": [{"text": body, "verdict": "supported", "source": "db_tool"}], "verdict": "supported"}
        )

    runtime = LiveRuntime(
        "intake",
        owner_engine=owner_engine,
        router=fake_router_by_class(
            agent=FakeGateway(generate),
            grounding=FakeGateway(check),
            safety=FakeGateway(
                [
                    '{"flagged":false,"category":"none"}',
                    '{"verdict":"allow","rule":null,"reason":"Synthetic ordinary reply"}',
                ]
            ),
        ),
    )
    scored = run_case(case, runtime.execute)
    assert scored.passed, scored
    agent = next(r for r in scored.diagnostics.runs if r.use_case == "intake")
    assert agent.steps == ["tool_call:check_stock:-", "tool_call:propose_reply:-", "final:-:-"]


def test_stock_query_is_bounded_scoped_and_rejects_ambiguous_arguments(owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "check_stock", {"query": "Chain 11-speed"}))
    assert len(data["items"]) == 1 and data["items"][0]["item_name"] == "Chain 11-speed"
    for args in (
        {},
        {"query": " "},
        {"item_ids": [str(demo.items["Chain 11-speed"])], "query": " "},
        {"item_ids": [str(demo.items["Chain 11-speed"])], "query": "Chain"},
        {"query": "a" * 101},
    ):
        assert call(ctx, state, "check_stock", args)["error"] == "platform.validation_error"
    other_ctx, other_state = assistant(demo.other)
    missing = ok(call(other_ctx, other_state, "check_stock", {"query": "Chain 11-speed"}))
    assert missing["items"] == [] and missing["not_found"] is True
    assert "do not infer zero stock" in missing["next_action"]
    limited_ctx, limited_state = assistant(demo.business, permissions=frozenset({"inventory.stock.read"}))
    assert call(limited_ctx, limited_state, "check_stock", {"query": "Chain"})["error"] == "auth.permission_denied"


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_ordinary_reply_requires_a_model_final(owner_engine: Engine, repair_succeeds: bool) -> None:
    demo = seed_demo(owner_engine)
    conv, msg = inbound(demo.business, body="Which day can I bring my bicycle?")
    gateway = FakeGateway(
        [
            step_tool("propose_reply", conversation_id=conv, text="Which service would you like?"),
            "{}",
            step_final("Prepared a question.") if repair_succeeds else "{}",
        ]
    )
    result = intake.run(
        msg,
        demo.business,
        router=fake_router_by_class(
            agent=gateway,
            grounding=FakeGateway(['{"claims":[],"verdict":"supported"}']),
            safety=FakeGateway(
                [
                    '{"flagged":false,"category":"none"}',
                    '{"verdict":"allow","rule":null,"reason":"Synthetic ordinary reply"}',
                ]
            ),
        ),
    )
    assert len(gateway.calls) == 3 and len(result.proposal_ids) == 1
    assert result.status == ("succeeded" if repair_succeeds else "failed")
    assert (result.final is not None) is repair_succeeds
    with owner_engine.connect() as db:
        kinds = list(
            db.scalars(text("select kind from ai_run_steps where run_id=:r order by step_no"), {"r": result.run_id})
        )
    assert kinds == (["tool_call", "repair", "final"] if repair_succeeds else ["tool_call", "repair", "repair"])


def test_real_handover_has_a_server_final_without_another_model_call(owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    conv, msg = inbound(demo.business, body="Please ask a person to help.")
    gateway = FakeGateway(
        [step_tool("propose_reply", conversation_id=conv, text="A person will help.", request_handoff=True)]
    )
    result = intake.run(
        msg,
        demo.business,
        router=fake_router_by_class(
            agent=gateway,
            safety=FakeGateway(
                [
                    '{"flagged":false,"category":"none"}',
                    '{"verdict":"allow","rule":null,"reason":"Synthetic ordinary reply"}',
                ]
            ),
        ),
    )
    assert result.status == "succeeded" and len(gateway.calls) == 1
    assert result.final and result.final.needs_human and result.extra["handoff_requested"]
    with owner_engine.connect() as db:
        assert db.scalar(text("select result->>'needs_human' from ai_runs where id=:r"), {"r": result.run_id}) == "true"
        assert (
            db.scalar(
                text("select count(*) from ai_runs where business_id=:b and use_case='grounding'"), {"b": demo.business}
            )
            == 0
        )


@pytest.mark.parametrize(
    "shape,diagnostic",
    [
        ("object_source", "string_type@claims.0.source"),
        ("overall_verdict", "missing@verdict"),
        ("pipe_enum", "literal_error@verdict"),
        ("too_many_claims", "too_long@claims"),
        ("long_claim", "string_too_long@claims.0.text"),
        ("truncated", "json_invalid"),
    ],
)
def test_brake_09_checker_shapes_fail_closed_with_precise_safe_diagnostics(
    shape: str,
    diagnostic: str,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("External calls forbidden in fake-model tests")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    case = next(c for c in load_cases("intake") if c.id == "intake-brake-09")
    turn = 0

    def generate(request: ChatRequest) -> str:
        nonlocal turn
        with owner_engine.connect() as db:
            conv = db.scalar(text("select id from conversations where subject=:s"), {"s": case.id})
        reply = step_tool("propose_reply", conversation_id=conv, text="Brake service takes 60 minutes.")
        turn += 1
        if turn == 1:
            return json.dumps({**json.loads(reply), "confidence": 0.9})
        return reply if turn == 2 else step_final("Prepared the reply for review.")

    claim: dict[str, Any] = {"text": "Brake service takes 60 minutes.", "verdict": "supported", "source": "db_tool"}
    value: dict[str, Any] = {"claims": [claim], "verdict": "supported"}
    if shape == "object_source":
        claim["source"] = {"db_tool": "PRIVATE_MARKER"}
    elif shape == "overall_verdict":
        value["overall_verdict"] = value.pop("verdict")
    elif shape == "pipe_enum":
        value["verdict"] = "supported|unsupported|contradicted"
    elif shape == "too_many_claims":
        value["claims"] = [claim] * 9
    elif shape == "long_claim":
        claim["text"] = "PRIVATE_MARKER" * 30
    bad = json.dumps(value) if shape != "truncated" else ('{"claims":[{"text":"' + "PRIVATE_MARKER" * 250)[:3200]
    checker = FakeGateway([bad, bad])
    runtime = LiveRuntime(
        "intake",
        owner_engine=owner_engine,
        router=fake_router_by_class(
            agent=FakeGateway(generate),
            grounding=checker,
            safety=FakeGateway(
                [
                    '{"flagged":false,"category":"none"}',
                    '{"verdict":"allow","rule":null,"reason":"Synthetic ordinary reply"}',
                ]
            ),
        ),
    )
    outcome = runtime.execute(case)
    assert outcome.status == "succeeded" and outcome.proposals[0].payload["grounding"]["verdict"] == "unavailable"
    assert outcome.output["autonomy_outcomes"] == ["fell_back"]
    assert "grounding_unavailable" in outcome.output["autonomy_reasons"]
    run = next(r for r in outcome.diagnostics.runs if r.use_case == "grounding")
    assert run.status == "failed" and len(run.steps) == 2
    assert all(diagnostic in s and "output_tokens=" in s and "output_cap=800" in s for s in run.steps)
    if shape == "truncated":
        assert all("output_tokens=800" in s for s in run.steps)
    assert "PRIVATE_MARKER" not in outcome.diagnostics.model_dump_json()
    assert "PRIVATE_MARKER" not in checker.calls[1].messages[-1].content
    agent = next(r for r in outcome.diagnostics.runs if r.use_case == "intake")
    assert "extra_forbidden@tool_call.confidence" in agent.steps[0]
