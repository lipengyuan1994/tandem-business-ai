"""C1: real PostgreSQL policy, publication, visibility and threaded Stop races; local fake models."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.types import ProviderError
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.tests.tool_helpers import Demo, seed_demo
from app.modules.ai.usecases import intake
from app.modules.approvals import engine
from app.modules.approvals import public as approvals
from app.modules.conversations import executors, publication
from app.modules.conversations import public as conversations
from app.modules.conversations.models import Message
from app.modules.conversations.schemas import SendReplyPayload
from app.modules.identity import public as identity
from app.modules.identity.testing import login_as
from app.platform import clock, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.registry import Registry

ACTION = "conversations.send_reply"
CHAT = "/api/v1/public/example-cycle-works/chat"


@pytest.fixture
def setup(owner_engine: Engine, registry: Registry) -> tuple[Demo, Context]:
    demo = seed_demo(owner_engine)
    with owner_engine.connect() as db:
        user = db.scalar(text("select id from users where email='morgan@example.com'"))
    ctx = Context("c1", "user", user, demo.business, identity.ROLES["owner"])
    return demo, ctx


def rule(ctx: Context, **changes: Any) -> None:
    with transaction(ctx) as db:
        saved = next(r for r in approvals.list_autonomy_rules(db, ctx).items if r.action_type == ACTION)
        approvals.put_autonomy_rule(
            db,
            ctx,
            approvals.RulePut(
                **{
                    "action_type": ACTION,
                    "level": "act_notify",
                    "shadow": True,
                    "daily_cap": 10,
                    "active": True,
                    "version": saved.version,
                    **changes,
                }
            ),
        )


def draft(
    demo: Demo,
    *,
    conv: uuid.UUID | None = None,
    msg: uuid.UUID | None = None,
    grounding: str = '{"claims":[],"verdict":"supported"}',
    safety: bool = True,
    failure: bool = False,
    body: str = "Which service would you like?",
) -> uuid.UUID:
    if conv is None:
        conv, msg = inbound(demo.business, body="Please help me choose a service.")
    assert msg is not None
    checker = FakeGateway([ProviderError("timeout")] if failure else [grounding, grounding])
    routes = {
        "agent": FakeGateway([step_tool("propose_reply", conversation_id=conv, text=body), step_final("Draft ready")]),
        "grounding": checker,
    }
    if safety:
        routes["safety"] = FakeGateway(['{"flagged":false,"category":"none"}'])
    result = intake.run(msg, demo.business, router=fake_router_by_class(**routes))
    assert result.status == "succeeded", result
    assert len(result.proposal_ids) == 1
    return result.proposal_ids[0]


def observed(owner_engine: Engine, proposal: uuid.UUID) -> Any:
    with owner_engine.connect() as db:
        return db.execute(
            text(
                "select p.status,p.current_revision_id,r.payload,e.outcome,e.reasons "
                "from action_proposals p join proposal_revisions r on r.id=p.current_revision_id "
                "left join autonomy_evaluations e on e.proposal_id=p.id where p.id=:p"
            ),
            {"p": proposal},
        ).one()


def execute(owner_engine: Engine, demo: Demo, proposal: uuid.UUID) -> uuid.UUID:
    row = observed(owner_engine, proposal)
    assert row.status == "approved"
    assert engine.execute(demo.business, proposal, row.current_revision_id) == "succeeded"
    with owner_engine.connect() as db:
        return db.scalar(text("select id from messages where proposal_id=:p"), {"p": proposal})


def test_reply_shadow_no_claim_and_daily_cap(setup: tuple[Demo, Context], owner_engine: Engine) -> None:
    demo, ctx = setup
    # Cycle Works now starts live; explicitly select shadow for this test.
    rule(ctx, daily_cap=1)
    p = draft(demo)
    assert observed(owner_engine, p).outcome == "would_act"
    assert observed(owner_engine, p).status == "pending_review"
    assert observed(owner_engine, p).payload["grounding"] == {"claims": [], "verdict": "supported"}
    p2 = draft(demo)
    assert observed(owner_engine, p2).reasons == ["cap_reached"]
    with transaction(ctx) as db:
        activity = approvals.autonomy_activity(db, ctx)
        assert {i.proposal_id for i in activity.items} >= {p, p2}


@pytest.mark.parametrize(
    "verdict,reason",
    [("unsupported", "ungrounded"), ("contradicted", "contradicted"), ("unavailable", "grounding_unavailable")],
)
def test_grounding_fallback_and_suggestion(
    setup: tuple[Demo, Context], owner_engine: Engine, verdict: str, reason: str
) -> None:
    demo, _ = setup
    p = draft(demo, grounding='{"claims":[],"verdict":"' + verdict + '"}')
    row = observed(owner_engine, p)
    assert row.status == "pending_review" and reason in row.reasons
    with owner_engine.connect() as db:
        count = db.scalar(text("select count(*) from kb_suggestions"))
        assert count == (0 if verdict == "unavailable" else 1)


@pytest.mark.parametrize(
    "failure,safety,reason", [(True, True, "grounding_unavailable"), (False, False, "safety_unchecked")]
)
def test_checker_failure_and_missing_safety_fail_closed(
    setup: tuple[Demo, Context], owner_engine: Engine, failure: bool, safety: bool, reason: str
) -> None:
    demo, _ = setup
    p = draft(demo, failure=failure, safety=safety)
    row = observed(owner_engine, p)
    assert row.status == "pending_review" and reason in row.reasons


def test_staff_proposal_never_rule_decided(setup: tuple[Demo, Context], owner_engine: Engine) -> None:
    demo, ctx = setup
    conv, _ = inbound(demo.business)
    with transaction(ctx) as db:
        p = approvals.create_proposal(
            db, ctx, action_type=ACTION, payload={"conversation_id": str(conv), "body": "Staff draft"}, title="Staff"
        )
    assert observed(owner_engine, p.id).outcome is None


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("answers_message_id", None, "not_intake"),
        ("bundled_action", True, "bundled_action"),
        ("safety_clean", False, "safety_unchecked"),
        ("ai_generation", 99, "stale_reply"),
    ],
)
def test_each_reply_condition(
    setup: tuple[Demo, Context], owner_engine: Engine, field: str, value: Any, reason: str
) -> None:
    demo, ctx = setup
    p = draft(demo)
    payload = SendReplyPayload.model_validate(observed(owner_engine, p).payload)
    setattr(payload, field, value)
    ai = dataclasses.replace(ctx, actor_kind="assistant", user_id=None, on_behalf_of_user_id=None)
    with transaction(ctx) as db:
        assert reason in executors.autonomy_reasons(db, ai, payload)
        if field == "bundled_action":
            # A complete A0 evaluation must fall back, not merely a standalone predicate.
            made = approvals.create_proposal(
                db, ai, action_type=ACTION, payload=payload.model_dump(mode="json"), title="Bundled"
            )
    if field == "bundled_action":
        assert observed(owner_engine, made.id).outcome == "fell_back"
        assert "bundled_action" in observed(owner_engine, made.id).reasons


def test_policy_execution_withholds_until_due_and_public_cursor_does_not_skip(
    setup: tuple[Demo, Context], owner_engine: Engine, registry: Registry
) -> None:
    demo, ctx = setup
    rule(ctx, shadow=False)
    app = create_app(registry)
    visitor = TestClient(app, base_url="https://testserver")
    first = visitor.post(CHAT, json={"name": "Ada Example", "message": "Help me choose"}).json()
    conv, msg = uuid.UUID(first["conversation_id"]), uuid.UUID(first["messages"][0]["id"])
    p = draft(demo, conv=conv, msg=msg)
    mid = execute(owner_engine, demo, p)
    with transaction(ctx) as db:
        row = db.get(Message, mid)
        assert row.publication_state == "withheld" and row.outbox_message_id is None
        due = row.publish_at
        publication.publish(db, ctx, {"message_id": str(mid)})  # early attempts remain withheld
    assert len(visitor.get(CHAT).json()["messages"]) == 1
    staff = login_as(app, demo.business, ["owner"], user_id=ctx.user_id)
    dto = staff.get(f"/api/v1/conversations/{conv}").json()
    pending = next(m for m in dto["messages"] if m["id"] == str(mid))
    assert pending["sending_label"] == "Sending in 0:15"
    assert staff.get(f"/api/v1/approvals/proposals/{p}").json()["delivery_state"] == "withheld"
    before = visitor.get(CHAT).json()["next_cursor"]
    clock.set_clock(lambda: due)
    # Run the actual registered durable job through the worker transaction.
    with owner_engine.begin() as db:
        job = dict(
            db.execute(
                text(
                    "update jobs set status='running',locked_by='c1-test',locked_until=:due "
                    "where dedupe_key=:key returning id,business_id,kind,payload,attempts,max_attempts,locked_by"
                ),
                {"due": due, "key": f"publish:{mid}"},
            )
            .mappings()
            .one()
        )
    assert jobs.run_job(registry, job)
    released = visitor.get(CHAT, params={"cursor": before}).json()
    assert [m["body"] for m in released["messages"]] == ["Which service would you like?"]
    assert staff.post(f"/api/v1/conversations/messages/{mid}/stop").status_code == 409
    assert staff.get(f"/api/v1/approvals/proposals/{p}").json()["delivery_state"] == "published"


@pytest.mark.parametrize(
    "cause,reason", [("visitor", "stale_reply"), ("takeover", "human_mode"), ("closed", "conversation_closed")]
)
def test_publication_cancels_and_exceptions_explain(
    setup: tuple[Demo, Context], owner_engine: Engine, registry: Registry, cause: str, reason: str
) -> None:
    demo, ctx = setup
    rule(ctx, shadow=False)
    conv, msg = inbound(demo.business)
    p = draft(demo, conv=conv, msg=msg)
    mid = execute(owner_engine, demo, p)
    with transaction(ctx) as db:
        if cause == "takeover":
            conversations.take_over(db, ctx, conv)
        elif cause == "visitor":
            from app.modules.conversations import service
            from app.modules.conversations.repository import get_conversation

            service.visitor_message(db, ctx, get_conversation(db, ctx, conv), "Actually, a different question.")
        else:
            db.execute(text("update conversations set status='closed' where id=:c"), {"c": conv})
        due = db.get(Message, mid).publish_at
    clock.set_clock(lambda: due)
    with transaction(ctx) as db:
        publication.publish(db, ctx, {"message_id": str(mid)})
        row = db.get(Message, mid)
        assert row.publication_state == "cancelled" and row.cancellation_reason == reason
        from app.modules.conversations.repository import public_messages

        assert mid not in {m.id for m in public_messages(db, ctx, conv)[0]}
    staff = login_as(create_app(registry), demo.business, ["owner"], user_id=ctx.user_id)
    exceptions = staff.get("/api/v1/ai/exceptions").json()["items"]
    assert any(i["id"] == str(mid) and i["failure_code"] == reason for i in exceptions)


def test_manual_staff_reply_immediate(setup: tuple[Demo, Context]) -> None:
    demo, ctx = setup
    conv, _ = inbound(demo.business)
    with transaction(ctx) as db:
        sent = conversations.send_reply(db, ctx, conv, "A staff reply")
        assert sent.publication_state == "published" and sent.publish_at is None
        from app.modules.conversations.repository import public_messages

        assert sent.id in {m.id for m in public_messages(db, ctx, conv)[0]}


def test_booking_with_reply_stays_ask(setup: tuple[Demo, Context], owner_engine: Engine) -> None:
    from app.modules.ai.tests.test_intake_agent import booking_steps, scripted

    demo, ctx = setup
    rule(ctx, shadow=False)
    conv, msg = inbound(demo.business, body="Brake service next Tuesday morning, please.")
    result = intake.run(
        msg,
        demo.business,
        router=fake_router_by_class(
            agent=scripted(*booking_steps(demo, conv, existing=True), step_final("Booking for review")),
            safety=FakeGateway(['{"flagged":false,"category":"none"}']),
        ),
    )
    assert len(result.proposal_ids) == 1
    row = observed(owner_engine, result.proposal_ids[0])
    assert row.status == "pending_review" and row.outcome is None and row.payload["reply"]


def test_staff_assisted_reply_falls_back_not_intake(setup: tuple[Demo, Context], owner_engine: Engine) -> None:
    from app.modules.ai.tests.tool_helpers import assistant, call, ok

    demo, ctx = setup
    ai, state = assistant(demo.business, on_behalf_of_user_id=ctx.user_id)
    conv, _ = inbound(demo.business)
    ok(call(ai, state, "get_conversation", {"conversation_id": str(conv)}))
    p = ok(call(ai, state, "propose_reply", {"conversation_id": str(conv), "text": "Staff-assisted draft"}))
    row = observed(owner_engine, uuid.UUID(p["proposal_id"]))
    assert row.status == "pending_review" and "not_intake" in row.reasons


def test_claim_cap_failure_keeps_proposal_for_staff(setup: tuple[Demo, Context], owner_engine: Engine) -> None:
    demo, _ = setup
    claim = {"text": "A synthetic claim", "source": "none", "verdict": "supported"}
    import json

    p = draft(demo, grounding=json.dumps({"claims": [claim] * 9, "verdict": "supported"}))
    row = observed(owner_engine, p)
    assert row.status == "pending_review" and row.payload["grounding"]["verdict"] == "unavailable"
    assert "grounding_unavailable" in row.reasons


def test_tied_clock_and_reversed_message_id_still_cancel_stale_reply(
    setup: tuple[Demo, Context], owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.conversations import service
    from app.modules.conversations.repository import get_conversation

    demo, ctx = setup
    rule(ctx, shadow=False)
    conv, msg = inbound(demo.business)
    p = draft(demo, conv=conv, msg=msg)
    mid = execute(owner_engine, demo, p)
    # Both messages have the same application timestamp; force the later UUID to sort before the first.
    monkeypatch.setattr(service, "new_id", lambda: uuid.UUID(int=1))
    with transaction(ctx) as db:
        new = service.visitor_message(db, ctx, get_conversation(db, ctx, conv), "A newer question")
        assert new.id < msg
        assert new.created_at == db.get(Message, msg).created_at
        assert new.ai_generation > db.get(Message, msg).ai_generation
        due = db.get(Message, mid).publish_at
    clock.set_clock(lambda: due)
    with transaction(ctx) as db:
        publication.publish(db, ctx, {"message_id": str(mid)})
        assert db.get(Message, mid).cancellation_reason == "stale_reply"


@pytest.mark.parametrize("category", ["complaint", "warranty", "dispute"])
def test_critical_category_never_acts_under_live_reply_rule(
    setup: tuple[Demo, Context], owner_engine: Engine, category: str
) -> None:
    demo, ctx = setup
    conv, msg = inbound(demo.business)
    ai = dataclasses.replace(ctx, actor_kind="assistant", user_id=None, on_behalf_of_user_id=None)
    with transaction(ctx) as db:
        source = conversations.get_message_context(db, ctx, msg)
        made = approvals.create_proposal(
            db,
            ai,
            action_type=ACTION,
            title="Critical customer question",
            payload={
                "conversation_id": str(conv),
                "body": "A person will help you.",
                "answers_message_id": str(msg),
                "ai_generation": source.message_ai_generation,
                "safety_clean": True,
                "care_category": category,
                "grounding": {"verdict": "supported", "claims": []},
            },
        )
    row = observed(owner_engine, made.id)
    assert row.status == "pending_review" and row.outcome == "fell_back"
    assert row.reasons == ["critical_topic"]


def test_guardrail_change_during_stop_delay_cancels_publication(
    setup: tuple[Demo, Context], owner_engine: Engine
) -> None:
    demo, ctx = setup
    p = draft(demo)
    mid = execute(owner_engine, demo, p)
    with owner_engine.begin() as db:
        db.execute(
            text(
                "update business_settings set settings=jsonb_set(settings,'{ai_reply_guardrail}',:rules) "
                "where business_id=:b"
            ),
            {"b": demo.business, "rules": '"Staff must approve all replies"'},
        )
    with transaction(ctx) as db:
        due = db.get(Message, mid).publish_at
    clock.set_clock(lambda: due)
    with transaction(ctx) as db:
        publication.publish(db, ctx, {"message_id": str(mid)})
        row = db.get(Message, mid)
        assert row.publication_state == "cancelled" and row.cancellation_reason == "guardrail_changed"
        from app.modules.conversations.repository import public_messages

        assert mid not in {m.id for m in public_messages(db, ctx, row.conversation_id)[0]}
