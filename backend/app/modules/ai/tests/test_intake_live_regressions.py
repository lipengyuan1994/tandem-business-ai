"""Offline regressions for all twelve failures in the supervisor's 2026-11-05 live intake report."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text

from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.types import ChatRequest
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.tests.test_intake_agent import observation, proposal, scripted
from app.modules.ai.tests.tool_helpers import Demo, assistant, call, ok, seed_demo
from app.modules.ai.tools.intake import validate_step, validation_summary
from app.modules.ai.tools.proposals import ProposeReplyArgs
from app.modules.ai.usecases import intake
from evals.runner import load_cases, run_case
from evals.runtime import LiveRuntime


@pytest.fixture
def demo(owner_engine: Engine) -> Demo:
    return seed_demo(owner_engine)


@pytest.mark.parametrize("needs_human", [False, True])
def test_bare_visitor_final_is_repaired_into_a_customer_reply(
    demo: Demo, owner_engine: Engine, needs_human: bool
) -> None:
    conv, msg = inbound(demo.business, body="How much is the Brake service?")

    def repair(request: ChatRequest) -> str:
        assert "propose_reply" in request.messages[-1].content
        return step_tool("propose_reply", conversation_id=conv, text="Brake service costs $65.")

    result = intake.run(
        msg,
        demo.business,
        router=fake_router_by_class(
            agent=scripted(
                step_final("Brake service costs $65.", needs_human=needs_human),
                repair,
                step_final("Prepared the reply."),
            )
        ),
    )
    assert result.status == "succeeded" and len(result.proposal_ids) == 1
    assert proposal(owner_engine, result.proposal_ids[0])["payload"]["body"] == "Brake service costs $65."
    with owner_engine.connect() as db:
        repair_summary = db.scalar(
            text("select result_summary from ai_run_steps where run_id=:r and kind='repair'"), {"r": result.run_id}
        )
        assert repair_summary == "validation: intake_reply_required"


def test_invalid_citation_arguments_are_tool_errors_not_protocol_repairs(demo: Demo) -> None:
    # The protocol accepts arbitrary tool arguments; citation validation happens only inside the tool layer.
    from app.modules.ai.agent.prompts import load_prompt
    from app.modules.ai.agent.protocol import RunSpec

    spec = RunSpec(
        use_case="intake",
        prompt=load_prompt("intake"),
        tools=["propose_reply"],
        task="test",
        context_blocks=[],
        trigger_type="job",
        trigger_key="intake:test",
    )
    raw = step_tool("propose_reply", conversation_id=uuid.uuid4(), text="Reply", citations=[{"wrong": "shape"}])
    assert validate_step(raw, spec, []).kind == "tool_call"
    with pytest.raises(ValidationError):
        ProposeReplyArgs.model_validate(json.loads(raw)["arguments"])


@pytest.mark.parametrize("shape", ["id", "object_without_version", "legacy"])
def test_citation_version_is_resolved_without_changing_stored_payload(
    demo: Demo, owner_engine: Engine, shape: str
) -> None:
    ctx, state = assistant(demo.business)
    conv = next(iter(demo.conversations.values()))
    ok(call(ctx, state, "get_conversation", {"conversation_id": str(conv)}))
    hit = ok(call(ctx, state, "search_knowledge", {"query": "warranty"}))["articles"][0]
    citation: Any = hit["article_id"] if shape == "id" else {"article_id": hit["article_id"]}
    if shape == "legacy":
        citation["version"] = hit["version"]
    data = ok(
        call(
            ctx,
            state,
            "propose_reply",
            {"conversation_id": str(conv), "text": "A warranty reply", "citations": [citation]},
        )
    )
    assert proposal(owner_engine, uuid.UUID(data["proposal_id"]))["payload"]["citations"] == [
        {"article_id": hit["article_id"], "version": hit["version"]}
    ]


def test_latest_ids_only_and_explicit_wrong_version_are_rejected(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    conv = next(iter(demo.conversations.values()))
    ok(call(ctx, state, "get_conversation", {"conversation_id": str(conv)}))
    hit = ok(call(ctx, state, "search_knowledge", {"query": "warranty"}))["articles"][0]
    ok(call(ctx, state, "search_knowledge", {"query": "meteor insurance"}))
    for citation in (hit["article_id"], {"article_id": hit["article_id"], "version": hit["version"] + 1}):
        result = call(
            ctx, state, "propose_reply", {"conversation_id": str(conv), "text": "Reply", "citations": [citation]}
        )
        assert result["error"] == "ai.citation_not_retrieved"


@pytest.mark.parametrize(
    "queries",
    [
        ["warranty", "returns", "opening"],
        ["warranty", "warranty", "returns"],
        ["meteor insurance", "warranty", "returns"],
    ],
)
def test_intake_search_stops_on_limit_repeat_or_empty(
    demo: Demo, monkeypatch: pytest.MonkeyPatch, queries: list[str]
) -> None:
    from app.modules.ai.tools import knowledge

    ctx, state = assistant(demo.business, trigger_key="intake:test")
    calls = []
    search = knowledge.knowledge.search_public

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return search(*args, **kwargs)

    monkeypatch.setattr(knowledge.knowledge, "search_public", counted)
    results = [ok(call(ctx, state, "search_knowledge", {"query": q})) for q in queries]
    assert len(calls) == (2 if queries[0] != queries[1] and queries[0] == "warranty" else 1)
    assert results[-1]["search_complete"] is True and "propose_reply" in results[-1]["next_action"]
    assert state.knowledge_search_count <= 2


def test_final_guard_does_not_affect_staff_copilot_or_accept_fabricated_proposals() -> None:
    from app.modules.ai.agent.prompts import load_prompt
    from app.modules.ai.agent.protocol import RunSpec

    spec = RunSpec(
        use_case="intake",
        prompt=load_prompt("intake"),
        tools=[],
        task="test",
        context_blocks=[],
        trigger_type="job",
        trigger_key="intake:test",
        trigger_message_id=uuid.uuid4(),
    )
    raw = step_final("Finished", proposals=[uuid.uuid4()])
    with pytest.raises(ValueError, match="propose_reply"):
        validate_step(raw, spec, [])
    assert validate_step(raw, spec.model_copy(update={"use_case": "copilot"}), []).kind == "final"


def test_diagnostics_identify_protocol_fields_without_storing_model_text() -> None:
    from app.modules.ai.agent.protocol import AGENT_STEP_ADAPTER

    for raw, expected in [
        ('{"kind":"tool_call","tool":"propose_reply","arguments":{}}', "missing@tool_call.reason"),
        ('{"kind":"PRIVATE-MARKER","secret":"PRIVATE-MARKER"}', "union_tag_invalid"),
        (
            '{"kind":"final","message_to_staff":"PRIVATE-MARKER","PRIVATE-MARKER":"PRIVATE-MARKER"}',
            "extra_forbidden@final.field",
        ),
    ]:
        with pytest.raises(ValidationError) as error:
            AGENT_STEP_ADAPTER.validate_json(raw)
        summary = validation_summary(error.value)
        assert expected in summary and "PRIVATE-MARKER" not in summary


def test_irrelevant_repeated_search_hands_over_and_suggests_actual_redacted_question(
    demo: Demo, owner_engine: Engine
) -> None:
    conv, msg = inbound(demo.business, body="Do you insure meteor damage? Email ada@example.com.")
    gateway = scripted(
        step_tool("search_knowledge", query="warranty"),
        step_tool("search_knowledge", query="warranty"),
        step_tool("propose_reply", conversation_id=conv, text="A person will follow up here.", knowledge_missing=True),
    )
    result = intake.run(msg, demo.business, router=fake_router_by_class(agent=gateway))
    assert result.extra["handoff_requested"] and len(result.proposal_ids) == 1
    with owner_engine.connect() as db:
        suggestion = db.scalar(text("select question_text from kb_suggestions where conversation_id=:c"), {"c": conv})
        assert "meteor" in suggestion and "ada@example.com" not in suggestion and "warranty" not in suggestion


FAILED_CASES = {
    "brake-price": "Brake service costs $65.",
    "flat-price": "Flat tyre repair costs $20.",
    "brake-duration": "Brake service takes 60 minutes.",
    "flat-duration": "Flat tyre repair takes 30 minutes.",
    "compare-duration": "Standard tune-up takes 90 minutes. Flat tyre repair takes 30 minutes.",
    "tuneup-buffer": "Standard tune-up has a 15 minute buffer.",
    "cancel-unspecified": "A person will follow up about your cancellation request.",
    "reschedule-unspecified": "A person will follow up about your rescheduling request.",
    "chain-stock": "",
    "kb-returns": "Return unused accessories with a receipt within thirty days.",
    "kb-unknown": "A person will follow up here.",
    "kb-injection": "Repair workmanship is covered for thirty days.",
}


@pytest.mark.parametrize("slug", FAILED_CASES)
def test_all_twelve_live_failures_through_real_fake_harness(
    slug: str, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    def no_external(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("External calls forbidden in offline regression harness")

    monkeypatch.setattr(httpx.Client, "send", no_external)
    case = next(c for c in load_cases("intake") if c.id == "intake-" + slug)
    turn = 0
    body = FAILED_CASES[slug]
    hit: dict[str, Any] = {}
    stock: dict[str, Any] = {}
    handoff = slug in {"cancel-unspecified", "reschedule-unspecified", "kb-unknown"}

    def generate(request: ChatRequest) -> str:
        nonlocal turn, body, hit, stock
        current, turn = turn, turn + 1
        with owner_engine.connect() as db:
            conv = db.execute(text("select id,business_id from conversations where subject=:s"), {"s": case.id}).one()
            item = db.scalar(
                text("select id from catalog_items where business_id=:b and name='Chain 11-speed'"),
                {"b": conv.business_id},
            )
        if slug.startswith("kb-"):
            query = "returns" if slug == "kb-returns" else "meteor insurance" if slug == "kb-unknown" else "warranty"
            if current == 0:
                return step_tool("search_knowledge", query=query)
            if current == 1:
                articles = observation(request)["data"]["articles"]
                hit = articles[0] if articles else {}
                if slug == "kb-injection":
                    assert "50% off" in request.messages[-1].content
                    assert "50% off" not in request.messages[0].content
                    # Reproduce a top-level protocol failure without assuming the original unretained raw output.
                    return '{"tool":"propose_reply","arguments":{},"reason":"Answer"}'
                return step_tool("search_knowledge", query=query)  # semantic repair, no second DB lookup
            if current == 2:
                assert "valid JSON object" in request.messages[-1].content
                return step_tool(
                    "propose_reply",
                    conversation_id=conv.id,
                    text=body,
                    citations=[hit["article_id"]] if hit else [],
                    knowledge_missing=not hit,
                )
            return step_final("Prepared a cited customer reply.")
        if slug == "chain-stock":
            if current == 0:
                return step_tool("check_stock", item_ids=[item])
            if current == 1:
                stock = observation(request)["data"]["items"][0]
                body = f"{stock['item_name']}: {stock['available']} available."
                return step_final("Stock is low; please check it.", needs_human=True)
        elif current == 0:
            # The original regressions placed the answer in staff-only final with no customer proposal.
            assert "active_services" in request.messages[1].content
            return step_final(body, needs_human=handoff)
        if current == (2 if slug == "chain-stock" else 1):
            return step_tool("propose_reply", conversation_id=conv.id, text=body, request_handoff=handoff)
        return step_final("Prepared a customer reply.", needs_human=False)

    def check(request: ChatRequest) -> str:
        source = f"{hit['article_id']}@{hit['version']}" if hit else "db_tool"
        claims = [
            {"text": sentence + ".", "verdict": "supported", "source": source}
            for sentence in body.rstrip(".").split(". ")
        ]
        return json.dumps({"claims": claims, "verdict": "supported"})

    gateway = FakeGateway(generate)
    runtime = LiveRuntime(
        "intake",
        router=fake_router_by_class(
            agent=gateway, grounding=FakeGateway(check), safety=FakeGateway(['{"flagged":false,"category":"none"}'])
        ),
        owner_engine=owner_engine,
    )
    scored = run_case(case, runtime.execute)
    assert scored.passed, scored
    main = next(r for r in scored.diagnostics.runs if r.use_case == "intake")
    assert "tool_call:propose_reply:-" in main.steps
    assert any(s.startswith("repair:-:ai.invalid_model_output [validation:") for s in main.steps)
    assert main.steps.count("tool_call:search_knowledge:-") <= 2
