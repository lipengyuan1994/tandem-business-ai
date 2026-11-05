"""Offline integration checks for v3 flows; FakeGateway scripts, rather than evaluates, decisions."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text

from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import RunResult
from app.modules.ai.gateway.types import ChatRequest
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_intake import inbound, read_context, run_row
from app.modules.ai.tests.test_intake_agent import REPLY, TUESDAY, observation, proposal, scripted
from app.modules.ai.tests.tool_helpers import Demo, seed_demo
from app.modules.ai.usecases import intake
from app.modules.conversations import public as conversations
from app.platform.db import transaction


@pytest.fixture
def demo(owner_engine: Engine) -> Demo:
    return seed_demo(owner_engine)


def test_intake_loads_v3_and_keeps_older_versions() -> None:
    assert load_prompt("intake").version == 3
    assert load_prompt("intake").output == "AgentStep"
    assert [load_prompt("intake", version).version for version in (1, 2)] == [1, 2]


def assert_reply_draft(
    owner_engine: Engine, demo: Demo, conv: uuid.UUID, result: RunResult, body: str, *, needs_human: bool
) -> None:
    assert result.status == "succeeded" and len(result.proposal_ids) == 1
    assert result.final is not None and result.final.needs_human is needs_human
    assert result.final.proposal_ids == result.proposal_ids
    row = proposal(owner_engine, result.proposal_ids[0])
    assert row["action_type"] == "conversations.send_reply"
    assert row["payload"] == {"conversation_id": str(conv), "body": body}
    recorded = run_row(demo.business, result)
    assert (recorded.prompt_name, recorded.prompt_version, recorded.needs_human) == ("intake", 3, needs_human)
    assert recorded.result["needs_human"] is needs_human
    with owner_engine.connect() as db:
        assert (
            db.scalar(text("select status from action_proposals where id = :id"), {"id": result.proposal_ids[0]})
            == "pending_review"
        )
    with transaction(read_context(demo.business)) as db:
        thread = conversations.get_thread(db, read_context(demo.business), conv)
    assert not any(message.direction == "outbound" for message in thread.messages)


def reply_final(req: ChatRequest, *, needs_human: bool = False) -> str:
    data = observation(req)
    assert data["ok"]
    return step_final(
        "Prepared a reply for approval.", proposals=[uuid.UUID(data["data"]["proposal_id"])], needs_human=needs_human
    )


def test_intake_preloaded_booking_takes_five_steps(demo: Demo, owner_engine: Engine) -> None:
    conv, message = inbound(demo.business, channel="email", body="Brake service next Tuesday morning, please.")

    def hold(req: ChatRequest) -> str:
        data = observation(req)
        assert data["ok"]
        slot = next(slot for slot in data["data"]["slots"] if slot["starts_at"] == TUESDAY)
        return step_tool(
            "hold_slot",
            customer_id=demo.customers["Ada Example"],
            service_id=demo.items["Brake service"],
            resource_id=slot["resource_id"],
            starts_at=slot["starts_at"],
        )

    def book(req: ChatRequest) -> str:
        data = observation(req)
        assert data["ok"]
        return step_tool(
            "propose_booking", hold_id=data["data"]["hold_id"], reply={"conversation_id": conv, "body": REPLY}
        )

    def finish(req: ChatRequest) -> str:
        data = observation(req)
        assert data["ok"]
        return step_final("Prepared a brake booking for approval.", proposals=[uuid.UUID(data["data"]["proposal_id"])])

    gw = scripted(
        step_tool("check_stock", item_ids=[demo.items["Brake pads (pair)"]]),
        step_tool(
            "find_slots",
            service_id=demo.items["Brake service"],
            earliest="2026-11-10T09:00:00-07:00",
            latest="2026-11-10T13:00:00-07:00",
        ),
        hold,
        book,
        finish,
    )
    result = intake.run(message, demo.business, router=fake_router_by_class(agent=gw))
    assert result.status == "succeeded" and len(result.proposal_ids) == 1
    assert run_row(demo.business, result).steps == len(gw.calls) == 5
    row = proposal(owner_engine, result.proposal_ids[0])
    assert row["action_type"] == "scheduling.book_appointment"
    assert row["payload"]["reply"] == {"conversation_id": str(conv), "body": REPLY}
    assert {e["ref_type"] for e in row["evidence"]} >= {"message", "check_stock", "find_slots"}


@pytest.mark.parametrize("item", ["Brake pads (pair)", "Cable set", "Inner tube 700x28", "Chain 11-speed"])
def test_intake_stock_result_becomes_reply_draft(demo: Demo, owner_engine: Engine, item: str) -> None:
    conv, message = inbound(demo.business, channel="email", body=f"Do you have {item} in stock?")
    bodies: list[str] = []

    def reply(req: ChatRequest) -> str:
        data = observation(req)
        assert data["ok"]
        stock = data["data"]["items"][0]
        assert stock["item_id"] == str(demo.items[item])
        body = f"{stock['item_name']}: {stock['available']} {stock['unit']} currently available."
        bodies.append(body)
        return step_tool("propose_reply", conversation_id=conv, text=body)

    gw = scripted(
        step_tool("check_stock", item_ids=[demo.items[item]]),
        reply,
        reply_final,
    )
    result = intake.run(message, demo.business, router=fake_router_by_class(agent=gw))
    assert_reply_draft(owner_engine, demo, conv, result, bodies[0], needs_human=False)
    assert run_row(demo.business, result).steps == 3
    row = proposal(owner_engine, result.proposal_ids[0])
    assert {e["ref_type"] for e in row["evidence"]} >= {"message", "propose_reply"}


@pytest.mark.parametrize(
    ("body", "reply", "needs_human"),
    [
        ("I need Brake service but have not decided on a day.", "Which day would you prefer?", False),
        ("Book me next Tuesday; I have not decided which service.", "Which service would you like?", False),
        ("Brake service either Tuesday or Friday but need to confirm which day.", "Tuesday or Friday?", False),
        ("Help me decide between Brake service and Standard tune-up.", "What problem are you noticing?", False),
        ("My bike feels strange. What should I book?", "What feels unusual when you ride?", False),
        ("Which of my appointments should I ask about? I have not decided.", "Which appointment do you mean?", False),
        (
            "Cancel my appointment; I cannot remember the day.",
            "Thanks for letting us know about the cancellation. A person will follow up.",
            True,
        ),
        (
            "Move my appointment to next month; I do not know which appointment.",
            "Thanks for your rescheduling request. A person will follow up.",
            True,
        ),
        ("Please refund my last repair.", "Thanks for your refund request. A person will follow up.", True),
        ("Book a complete carbon frame repaint.", "Thanks for asking about repainting. A person will follow up.", True),
        ("I am unhappy with my last repair.", "Sorry to hear about your repair. A person will follow up.", True),
    ],
    ids=[
        "no-date",
        "no-service",
        "two-dates",
        "two-services",
        "unspecified-problem",
        "which-appointment",
        "cancel",
        "reschedule",
        "refund",
        "unknown-service",
        "complaint",
    ],
)
def test_intake_reply_preserves_human_flag_without_booking_or_sending(
    demo: Demo, owner_engine: Engine, body: str, reply: str, needs_human: bool
) -> None:
    conv, message = inbound(demo.business, channel="email", body=body)
    with owner_engine.connect() as db:
        before = db.execute(text("select id, status, starts_at, proposal_id from appointments order by id")).all()
    gw = scripted(
        step_tool("propose_reply", conversation_id=conv, text=reply),
        lambda req: reply_final(req, needs_human=needs_human),
    )
    result = intake.run(message, demo.business, router=fake_router_by_class(agent=gw))
    assert_reply_draft(owner_engine, demo, conv, result, reply, needs_human=needs_human)
    assert len(gw.calls) == run_row(demo.business, result).steps == 2
    assert gw.calls[0].messages[0].content.startswith(load_prompt("intake").text)
    with owner_engine.connect() as db:
        assert (
            db.execute(text("select id, status, starts_at, proposal_id from appointments order by id")).all() == before
        )
