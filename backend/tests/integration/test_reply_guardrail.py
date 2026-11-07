"""Offline wiring evidence; scripted verdicts do not measure semantic accuracy."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.types import ChatRequest, ProviderError
from app.modules.ai.reply_guardrail import check
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.tests.tool_helpers import seed_demo
from app.modules.ai.usecases import intake
from app.modules.approvals import engine
from app.modules.conversations import executors
from app.modules.conversations.schemas import SendReplyPayload
from app.platform.context import system_context
from app.platform.db import transaction
from evals.runner import Case, load_cases, run_case, summarize
from evals.runtime import LiveRuntime, validate_cases

ALLOW = '{"verdict":"allow","rule":null,"reason":"Ordinary question"}'
STAFF = '{"verdict":"needs_staff","rule":"Discounts","reason":"A person approves discounts"}'


def test_guardrail_fake_harness(owner_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_external(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("External calls forbidden")

    monkeypatch.setattr(httpx.Client, "send", no_external)
    cases = load_cases("guardrail")
    assert len(cases) >= 12
    validate_cases(cases)
    rows = []
    for case in cases:

        def reply(request: ChatRequest, case: Case = case) -> str:
            assert request.use_case == "reply_guardrail" and request.model == "fake-safety"
            content = request.messages[1].content
            assert "## owner_rules" in content and case.input["rules"] in content
            assert '<untrusted source="customer_message"' in content
            assert '<untrusted source="drafted_reply"' in content
            assert content.count("</untrusted>") == 2
            return ALLOW if case.expect.output["verdict"] == "allow" else STAFF

        runtime = LiveRuntime(
            "guardrail", router=fake_router_by_class(safety=FakeGateway(reply)), owner_engine=owner_engine
        )
        result = run_case(case, runtime.execute)
        assert result.passed, result
        rows.append(result)
    assert summarize(rows)["guardrail_pass_rate"] == 1
    assert summarize(rows)["thresholds_met"]


@pytest.mark.parametrize(
    "output",
    [
        "invalid",
        '{"verdict":"allow","rule":null,"reason":""}',
        '{"verdict":"maybe","rule":null,"reason":"ok"}',
        '{"verdict":"allow","rule":null,"reason":"ok","extra":true}',
        ProviderError("timeout"),
    ],
)
def test_failed_check_is_unavailable(owner_engine: Engine, output: Any) -> None:
    demo = seed_demo(owner_engine)
    gateway = FakeGateway([output, output])
    value = check(
        background_ctx_factory(demo.business),
        rules="Ask staff for refunds",
        message="Refund?",
        reply="Yes",
        trigger_key="failure",
        router=fake_router_by_class(safety=gateway),
    )
    assert value is None
    with owner_engine.connect() as db:
        assert (
            db.scalar(text("select sum(model_calls) from ai_chain_budgets where business_id=:b"), {"b": demo.business})
            >= 1
        )


def test_empty_rules_make_no_call(owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    gateway = FakeGateway([])
    assert (
        check(
            background_ctx_factory(demo.business),
            rules=None,
            message="Hi",
            reply="Hello",
            trigger_key="empty",
            router=fake_router_by_class(safety=gateway),
        )
        is None
    )
    assert not gateway.calls


@pytest.mark.parametrize("shadow", [True, False])
@pytest.mark.parametrize(
    "verdict,reason", [(ALLOW, None), (STAFF, "guardrail_needs_staff"), (None, "guardrail_unavailable")]
)
def test_intake_guardrail_payload_policy_budget_and_execution(
    owner_engine: Engine, shadow: bool, verdict: str | None, reason: str | None
) -> None:
    demo = seed_demo(owner_engine, guardrail=True)
    with owner_engine.begin() as db:
        db.execute(
            text(
                "update autonomy_rules set shadow=:shadow where business_id=:b "
                "and action_type='conversations.send_reply'"
            ),
            {"shadow": shadow, "b": demo.business},
        )
    conv, msg = inbound(demo.business, body="Which service?")

    def screen(request: ChatRequest) -> str:
        if request.use_case == "safety":
            return '{"flagged":false,"category":"none","care_category":"none"}'
        if verdict is None:
            raise ProviderError("timeout")
        return verdict

    safety = FakeGateway(screen)
    result = intake.run(
        msg,
        demo.business,
        router=fake_router_by_class(
            agent=FakeGateway(
                [step_tool("propose_reply", conversation_id=conv, text="Which service would you like?"), step_final()]
            ),
            grounding=FakeGateway(['{"verdict":"supported","claims":[]}']),
            safety=safety,
        ),
    )
    assert len(result.proposal_ids) == 1, result
    with owner_engine.connect() as db:
        row = db.execute(
            text(
                "select p.status,p.current_revision_id,r.payload,e.outcome,e.reasons from action_proposals p "
                "join proposal_revisions r on r.id=p.current_revision_id "
                "join autonomy_evaluations e on e.proposal_id=p.id where p.id=:p"
            ),
            {"p": result.proposal_ids[0]},
        ).one()
        assert (
            db.scalar(text("select sum(model_calls) from ai_chain_budgets where business_id=:b"), {"b": demo.business})
            >= 3
        )
        assert (
            db.scalar(
                text("select count(*) from ai_agent_usage_daily where business_id=:b and agent_key='front_desk'"),
                {"b": demo.business},
            )
            > 0
        )
    assert (
        row.payload["care_category"] == "none"
        and row.payload["guardrail_hash"]
        and row.payload["guardrail_version"] == 1
    )
    assert row.payload["guardrail"] == (json.loads(verdict) if verdict else None)
    assert row.outcome == ("fell_back" if reason else "would_act" if shadow else "acted")
    assert row.status == ("pending_review" if shadow or reason else "approved")
    assert (reason in row.reasons) if reason else not row.reasons
    if not shadow and not reason:
        with owner_engine.begin() as db:
            db.execute(
                text(
                    "update business_settings set settings=jsonb_set(settings,"
                    "'{ai_reply_guardrail}','\"All replies need staff\"') where business_id=:b"
                ),
                {"b": demo.business},
            )
        assert engine.execute(demo.business, result.proposal_ids[0], row.current_revision_id) == "pending_review"
        with owner_engine.connect() as db:
            assert (
                db.scalar(text("select count(*) from messages where proposal_id=:p"), {"p": result.proposal_ids[0]})
                == 0
            )
            assert "guardrail_changed" in db.scalar(
                text("select failure_code from action_proposals where id=:p"), {"p": result.proposal_ids[0]}
            )


@pytest.mark.parametrize("category", ["complaint", "warranty", "dispute", None])
def test_critical_topics_fail_even_with_supported_reply(owner_engine: Engine, category: str | None) -> None:
    demo = seed_demo(owner_engine)
    conv, msg = inbound(demo.business)
    payload = SendReplyPayload(
        conversation_id=conv,
        body="Hello",
        answers_message_id=msg,
        safety_clean=True,
        care_category=category,
        grounding={"verdict": "supported", "claims": []},
    )
    ctx = system_context(demo.business)
    with transaction(ctx) as db:
        assert "critical_topic" in executors.autonomy_reasons(db, ctx, payload)


def test_guardrail_over_budget_fails_closed_before_provider(owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    gateway = FakeGateway([])
    with owner_engine.begin() as db:
        db.execute(
            text(
                "update business_settings set settings=jsonb_set(settings,'{ai_daily_token_budget}','0') "
                "where business_id=:b"
            ),
            {"b": demo.business},
        )
    assert (
        check(
            background_ctx_factory(demo.business),
            rules="Ask about refunds",
            message="Refund?",
            reply="Yes",
            trigger_key="budget",
            router=fake_router_by_class(safety=gateway),
        )
        is None
    )
    assert not gateway.calls
    with owner_engine.connect() as db:
        run = db.execute(
            text("select status,error_code from ai_runs where business_id=:b and use_case='reply_guardrail'"),
            {"b": demo.business},
        ).one()
    assert run.status == "budget_exceeded" and run.error_code == "ai.budget_exceeded"


def test_guardrail_records_cost_agent_and_chain_attribution(owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    router = fake_router_by_class(safety=FakeGateway([ALLOW]))
    entry = router.routes.routes["safety"][0]
    entry.price_in_per_m = 1
    entry.price_out_per_m = 2
    assert (
        check(
            background_ctx_factory(demo.business),
            rules="Ask about refunds",
            message="Hours?",
            reply="Open at 9.",
            trigger_key="cost",
            router=router,
        )
        is not None
    )
    with owner_engine.connect() as db:
        run = db.execute(
            text(
                "select cost_micros,correlation_id,agent_key from ai_runs "
                "where business_id=:b and use_case='reply_guardrail'"
            ),
            {"b": demo.business},
        ).one()
        chain = db.execute(
            text("select model_calls,cost_micros from ai_chain_budgets where business_id=:b and correlation_id=:c"),
            {"b": demo.business, "c": run.correlation_id},
        ).one()
        agent_cost = db.scalar(
            text("select cost_micros from ai_agent_usage_daily where business_id=:b and agent_key='front_desk'"),
            {"b": demo.business},
        )
    assert run.agent_key == "front_desk" and run.cost_micros > 0
    assert chain.model_calls == 1 and chain.cost_micros == run.cost_micros == agent_cost


@pytest.mark.parametrize("field", ["care_category", "safety_clean", "guardrail", "guardrail_hash", "guardrail_version"])
def test_agent_cannot_supply_guardrail_or_care_facts(field: str) -> None:
    from uuid import uuid4

    from pydantic import ValidationError

    from app.modules.ai.tools.proposals import ProposeReplyArgs

    with pytest.raises(ValidationError):
        ProposeReplyArgs.model_validate({"conversation_id": str(uuid4()), "text": "Hello", field: "allow"})


def test_guarded_ordinary_reply_reaches_public_chat_automatically(owner_engine: Engine, registry: Any) -> None:
    from uuid import UUID

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.modules.conversations import publication
    from app.modules.conversations.models import Message
    from app.modules.identity import public as identity
    from app.platform import clock

    demo = seed_demo(owner_engine, guardrail=True)
    visitor = TestClient(create_app(registry), base_url="https://testserver")
    path = "/api/v1/public/example-cycle-works/chat"
    started = visitor.post(
        path, json={"name": "Example Visitor", "message": "What is the catalog price of brake service?"}
    )
    assert started.status_code == 201
    thread = started.json()
    conv, msg = UUID(thread["conversation_id"]), UUID(thread["messages"][0]["id"])
    body = "Brake service costs $65.00."
    result = intake.run(
        msg,
        demo.business,
        router=fake_router_by_class(
            agent=FakeGateway([step_tool("propose_reply", conversation_id=conv, text=body), step_final()]),
            grounding=FakeGateway(
                [
                    json.dumps(
                        {
                            "verdict": "supported",
                            "claims": [{"text": body, "verdict": "supported", "source": "db_tool"}],
                        }
                    )
                ]
            ),
            safety=FakeGateway(['{"flagged":false,"category":"none","care_category":"none"}', ALLOW]),
        ),
    )
    assert len(result.proposal_ids) == 1
    proposal = result.proposal_ids[0]
    with owner_engine.connect() as db:
        row = db.execute(
            text("select status,current_revision_id from action_proposals where id=:p"),
            {"p": proposal},
        ).one()
    assert row.status == "approved"
    assert engine.execute(demo.business, proposal, row.current_revision_id) == "succeeded"
    assert all(message["body"] != body for message in visitor.get(path).json()["messages"])
    ctx = system_context(demo.business, identity.ROLES["owner"])
    with transaction(ctx) as db:
        from sqlalchemy import select

        message = db.scalars(select(Message).where(Message.proposal_id == proposal)).one()
        assert message.publication_state == "withheld"
        assert (message.publish_at - clock.now()).total_seconds() == 15
        mid, due = message.id, message.publish_at
    clock.set_clock(lambda: due)
    with transaction(ctx) as db:
        publication.publish(db, ctx, {"message_id": str(mid)})
    assert any(message["body"] == body for message in visitor.get(path).json()["messages"])
