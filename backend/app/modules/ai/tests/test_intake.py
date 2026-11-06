from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, event, select, text

from app.modules.ai.agent.protocol import FinalStep, RunResult
from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.models import AiRun
from app.modules.ai.tests.helpers import fake_router_by_class, step_tool
from app.modules.ai.usecases import intake
from app.modules.approvals import public as approvals
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.org import public as org
from app.platform import jobs
from app.platform.context import Context, system_context
from app.platform.db import get_engine, transaction
from app.platform.registry import Registry


def read_context(business: uuid.UUID) -> Context:
    return system_context(
        business,
        frozenset(
            {"conversations.thread.read", "conversations.thread.write", "crm.customer.read", "crm.customer.write"}
        ),
    )


@pytest.fixture
def business() -> uuid.UUID:
    ctx = system_context(uuid.uuid4(), frozenset({"org.business.write"}))
    with transaction(ctx) as db:
        brief = org.create_business(
            db,
            ctx,
            name="Example Workshop",
            slug=f"example-{uuid.uuid4().hex}",
            timezone="America/Denver",
            currency="USD",
            locale="en-US",
        )
    return brief.id


@pytest.fixture(autouse=True)
def clean_router() -> Iterator[None]:
    set_router_for_tests(None)
    reset_router_cache()
    yield
    set_router_for_tests(None)
    reset_router_cache()


def inbound(business: uuid.UUID, **overrides: Any) -> tuple[uuid.UUID, uuid.UUID]:
    ctx = read_context(business)
    args = dict(
        channel="web_chat",
        author_kind="visitor",
        body="Can you repair my brakes?",
        visitor_name="Ada Example",
        visitor_email="ada@example.com",
        visitor_phone="+12025550123",
    )
    args.update(overrides)
    with transaction(ctx) as db:
        conv = conversations.start_conversation(db, ctx, **args)
        return conv, conversations.get_thread(db, ctx, conv).messages[0].id


def notes(business: uuid.UUID, conv: uuid.UUID) -> list[str]:
    with transaction(read_context(business)) as db:
        thread = conversations.get_thread(db, read_context(business), conv, last_n=10000)
    return [m.body for m in thread.messages if m.direction == "internal" and m.author_kind == "assistant"]


def run_row(business: uuid.UUID, result: RunResult) -> AiRun:
    with transaction(read_context(business)) as db:
        row = db.get(AiRun, result.run_id)
        assert row is not None
        db.expunge(row)
        return row


def trigger(registry: Registry, business: uuid.UUID, message: uuid.UUID, author: str) -> None:
    handler = registry.subscribers["conversations.message_received"]["ai.intake-trigger"]
    with transaction(read_context(business)) as db:
        handler(db, read_context(business), {"message_id": str(message), "author_kind": author}, uuid.uuid4())


@pytest.mark.parametrize("author", ["visitor", "customer"])
def test_intake_enqueued_once_per_inbound_message(
    registry: Registry, business: uuid.UUID, owner_engine: Engine, author: str
) -> None:
    message = uuid.uuid4()
    trigger(registry, business, message, author)
    trigger(registry, business, message, author)
    with owner_engine.connect() as db:
        rows = db.execute(text("select kind, payload, business_id, dedupe_key from jobs")).all()
    assert rows == [("ai.intake", {"message_id": str(message)}, business, f"intake:{message}")]


@pytest.mark.parametrize("author", ["staff", "assistant", "system"])
def test_intake_ignores_staff_assistant_system_messages(
    registry: Registry, business: uuid.UUID, owner_engine: Engine, author: str
) -> None:
    trigger(registry, business, uuid.uuid4(), author)
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from jobs")) == 0


def test_job_runs_without_open_transaction(
    registry: Registry, business: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = uuid.uuid4()
    active: set[Any] = set()
    seen = []
    engine = get_engine()

    def begin(conn: Any) -> None:
        active.add(conn)

    def end(conn: Any) -> None:
        active.discard(conn)

    def run(message_id: uuid.UUID, business_id: uuid.UUID) -> None:
        assert not active
        seen.append((message_id, business_id))

    monkeypatch.setattr(intake, "run", run)
    trigger(registry, business, message, "visitor")
    assert registry.jobs["ai.intake"].transactional is False
    event.listen(engine, "begin", begin)
    event.listen(engine, "commit", end)
    event.listen(engine, "rollback", end)
    try:
        claimed = jobs.claim("intake-test")
        assert claimed is not None
        assert jobs.run_job(registry, claimed)
    finally:
        event.remove(engine, "begin", begin)
        event.remove(engine, "commit", end)
        event.remove(engine, "rollback", end)
    assert seen == [(message, business)]


def test_open_proposal_skips_with_early_result(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    conv, message = inbound(business)
    calls = []

    def find(db: Any, ctx: Context, **kw: Any) -> list[Any]:
        calls.append(kw)
        return [object()]

    monkeypatch.setattr(intake.approvals, "find_open_proposals", find)
    gw = FakeGateway([])
    result = intake.run(message, business, router=fake_router_by_class(safety=gw, agent=gw))
    assert calls == [
        {"conversation_id": conv, "action_types": ["scheduling.book_appointment", "conversations.send_reply"]}
    ]
    assert result.extra == {"reason": "open_proposal"}
    row = run_row(business, result)
    assert (row.status, row.steps, row.needs_human, row.result) == ("succeeded", 0, True, {"reason": "open_proposal"})
    assert not gw.calls
    assert notes(business, conv) == [
        f"A proposal for this conversation is already waiting in Approvals. [intake:{message}]"
    ]


def test_open_proposal_guard_with_real_proposal(business: uuid.UUID, registry: Registry) -> None:
    conv, message = inbound(business)
    ctx = intake.background_ctx_factory(business)(uuid.uuid4())
    with transaction(ctx) as db:
        approvals.create_proposal(
            db, ctx, action_type="conversations.send_reply",
            payload={"conversation_id": str(conv), "body": "We can help with your brakes."},
            title="Reply to brake enquiry", conversation_id=conv,
        )  # fmt: skip
    result = intake.run(message, business, router=fake_router_by_class())
    assert result.extra == {"reason": "open_proposal"}
    assert run_row(business, result).steps == 0


def test_intake_job_completes_with_agent(business: uuid.UUID, registry: Registry, owner_engine: Engine) -> None:
    conv, message = inbound(business, channel="email")
    set_router_for_tests(
        fake_router_by_class(
            agent=FakeGateway(
                [
                    step_tool(
                        "propose_reply",
                        conversation_id=conv,
                        text="A person will check availability.",
                        request_handoff=True,
                    )
                ]
            )
        )
    )
    trigger(registry, business, message, "visitor")
    claimed = jobs.claim("intake-test")
    assert claimed is not None and jobs.run_job(registry, claimed)
    with owner_engine.connect() as db:
        assert db.scalar(text("select status from jobs where id = :id"), {"id": claimed["id"]}) == "succeeded"
    with transaction(read_context(business)) as db:
        row = db.scalar(select(AiRun).where(AiRun.trigger_key == f"intake:{message}"))
        assert row is not None and row.needs_human
        assert row.prompt_name == "intake" and row.steps == 1
        assert len(row.result["proposal_ids"]) == 1
        assert row.result["text"] == "Handed to a person; AI replies are paused." and row.result["needs_human"]
        assert conversations.get_thread(db, read_context(business), conv).ai_mode == "human"
    assert notes(business, conv) == [
        f"Handed to a person. AI replies are paused and the earlier reply draft was cancelled. [intake:{message}]"
    ]


def test_flagged_public_message_hands_off_without_agent_call(business: uuid.UUID) -> None:
    conv, message = inbound(business)
    safety = FakeGateway(['{"flagged":true,"category":"violence"}'])
    agent = FakeGateway([])
    result = intake.run(message, business, router=fake_router_by_class(safety=safety, agent=agent))
    assert result.extra == {"reason": "safety"}
    assert not agent.calls
    assert run_row(business, result).needs_human
    with transaction(read_context(business)) as db:
        assert conversations.get_thread(db, read_context(business), conv).status == "waiting_on_staff"
    assert notes(business, conv) == [f"This message was passed to a person without AI handling. [intake:{message}]"]


@pytest.mark.parametrize(
    "failure",
    [None, "ai.budget_exceeded", "ai.provider_unavailable", "ai.invalid_model_output", "ai.disabled_by_policy"],
)
def test_safety_unavailable_marks_unchecked_and_continues(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    conv, message = inbound(business)
    if failure:

        def screen(*args: Any, **kw: Any) -> None:
            raise AiError(failure)

        monkeypatch.setattr(intake.safety, "screen", screen)
    result = intake.run(
        message,
        business,
        router=fake_router_by_class(
            agent=FakeGateway(
                [
                    step_tool(
                        "propose_reply",
                        conversation_id=conv,
                        text="A person will check availability.",
                        request_handoff=True,
                    )
                ]
            )
        ),
    )
    assert result.extra["safety_unchecked"] is True and result.extra["handoff_requested"] is True
    assert len(result.proposal_ids) == 1 and result.extra["proposal_titles"]
    assert result.final is not None and result.final.needs_human


@pytest.mark.parametrize(
    ("scores", "kind"),
    [
        ([], "new"),
        ([0.49], "new"),
        ([0.5], "ambiguous"),
        ([0.79], "ambiguous"),
        ([0.8], "matched"),
        ([0.9, 0.8], "matched"),
        ([0.89, 0.8], "ambiguous"),
        ([0.8, 0.8], "ambiguous"),
        ([0.95, 0.8, 0.6], "matched"),
    ],
)
def test_customer_matching_rule_matched_new_ambiguous(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, scores: list[float], kind: str
) -> None:
    _, message = inbound(business, channel="email", visitor_email=None, visitor_phone=None)
    candidates = [
        crm.CustomerCandidate(customer_id=uuid.uuid4(), display_name=f"Ada Example {i}", score=s, reason="name")
        for i, s in enumerate(scores)
    ]
    queries = []

    def find(db: Any, ctx: Context, **kw: Any) -> list[crm.CustomerCandidate]:
        queries.append(kw)
        return list(reversed(candidates))

    monkeypatch.setattr(crm, "find_customer_candidates", find)
    seen = []
    original = intake.run_agent_step

    def step(factory: Any, msg: Any, match: Any, state: Any) -> RunResult:
        seen.append(match)
        return original(factory, msg, match, state)

    monkeypatch.setattr(intake, "run_agent_step", step)
    result = intake.run(message, business, router=fake_router_by_class())
    assert queries == [{"name": "Ada Example", "email": None, "phone": None}]
    assert seen[0].kind == kind
    assert seen[0].customer_id == (candidates[0].customer_id if kind == "matched" else None)
    if kind == "ambiguous":
        assert result.extra["reason"] == "ambiguous_customer"
    assert "safety_unchecked" not in result.extra


def test_linked_customer_takes_precedence(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = read_context(business)
    with transaction(ctx) as db:
        customer = crm.create_customer(db, ctx, crm.CustomerCreate(display_name="Linked Example"))
    _, message = inbound(business, channel="email", customer_id=customer.id)

    def forbidden(*args: Any, **kw: Any) -> Any:
        pytest.fail("linked conversations must not search for candidates")

    monkeypatch.setattr(crm, "find_customer_candidates", forbidden)
    original = intake.run_agent_step

    def step(factory: Any, msg: Any, match: Any, state: Any) -> RunResult:
        assert match.customer_id == customer.id and match.kind == "matched"
        return original(factory, msg, match, state)

    monkeypatch.setattr(intake, "run_agent_step", step)
    intake.run(message, business, router=fake_router_by_class())


def test_ambiguous_customer_note_lists_names_only(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    conv, message = inbound(business, channel="email")
    candidates = [
        crm.CustomerCandidate(customer_id=uuid.uuid4(), display_name=f"Example {i}", score=0.7, reason="email")
        for i in range(5)
    ]
    monkeypatch.setattr(crm, "find_customer_candidates", lambda *a, **kw: candidates)
    result = intake.run(message, business, router=fake_router_by_class())
    assert result.extra["reason"] == "ambiguous_customer"
    assert result.extra["candidates"] == ["Example 0", "Example 1", "Example 2"]
    assert notes(business, conv) == [
        "AI is turned off for this business, so no proposal was prepared. "
        "Several customers could match (Example 0, Example 1, Example 2). "
        f"Please link the right one. [intake:{message}]"
    ]


def test_one_outcome_note_per_message_even_on_retry(business: uuid.UUID) -> None:
    conv, message = inbound(business, channel="email")
    intake.run(message, business, router=fake_router_by_class())
    # The marker must still be found after it falls outside the default last-20 window.
    with transaction(read_context(business)) as db:
        for i in range(25):
            conversations.add_internal_note(db, read_context(business), conv, f"Staff note {i}", author_kind="staff")
    intake.run(message, business, router=fake_router_by_class())
    assert len(notes(business, conv)) == 1
    assert notes(business, conv)[0].endswith(f"[intake:{message}]")


def test_concurrent_retries_write_one_outcome_note(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    conv, message = inbound(business, channel="email")
    barrier = Barrier(2)
    original = intake._outcome_note

    def note(*args: Any) -> None:
        barrier.wait(timeout=10)
        original(*args)

    monkeypatch.setattr(intake, "_outcome_note", note)
    router = fake_router_by_class()
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(intake.run, message, business, router=router) for _ in range(2)]
        results = [attempt.result(timeout=20) for attempt in attempts]
    assert all(result.status == "policy_blocked" and result.error_code == "ai.not_configured" for result in results)
    assert len(notes(business, conv)) == 1


def test_visitor_contacts_not_in_any_model_request(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    _, message = inbound(business)
    gw = FakeGateway(['{"flagged":false,"category":"none"}'])
    original = intake.run_agent_step
    seen = []

    def step(factory: Any, msg: Any, match: Any, state: Any) -> RunResult:
        seen.append(state.server_facts)
        return original(factory, msg, match, state)

    monkeypatch.setattr(intake, "run_agent_step", step)
    result = intake.run(message, business, router=fake_router_by_class(safety=gw))
    assert seen == [
        {
            "visitor_name": "Ada Example",
            "visitor_email": "ada@example.com",
            "visitor_phone": "+12025550123",
            "ai_generation": 0,
            "safety_clean": True,
        }
    ]
    assert len(gw.calls) == 1
    requests = json.dumps([r.model_dump(mode="json") for r in gw.calls])
    for value in ("Ada Example", "ada@example.com", "+12025550123"):
        assert value not in requests
        assert value not in json.dumps(run_row(business, result).result)


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        ("policy_blocked", None, "AI is turned off for this business, so no proposal was prepared."),
        ("budget_exceeded", None, "AI allowance cannot fit another model call; please handle this message."),
        ("failed", None, "Could not prepare a proposal; please handle this message."),
        ("step_limit", None, "Could not prepare a proposal; please handle this message."),
        ("succeeded", "human", "Needs a person: Please check availability"),
        ("succeeded", "proposal", "Prepared for approval: Brake booking; Reply."),
    ],
)
def test_outcome_note_table(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, status: Any, reason: str | None, expected: str
) -> None:
    conv, message = inbound(business, channel="email")
    final = FinalStep(kind="final", needs_human=True, message_to_staff="Please check availability")
    result = RunResult(run_id=uuid.uuid4(), status=status, final=final, proposal_ids=[])
    if reason == "proposal":
        result.proposal_ids = [uuid.uuid4()]
        result.extra = {"proposal_titles": ["Brake booking", "Reply"]}
    monkeypatch.setattr(intake, "run_agent_step", lambda *a: result)
    intake.run(message, business, router=fake_router_by_class())
    assert notes(business, conv) == [f"{expected} [intake:{message}]"]


@pytest.mark.parametrize(
    ("name", "email", "phone", "expected"),
    [
        ("Bea Example", "bea@example.com", None, "new"),
        ("Ada Example", "bea@example.com", None, "new"),
        ("Ada Example", None, "+12025550199", "new"),
        ("Bea Example", "ADA@EXAMPLE.COM", None, "matched"),
        ("Bea Example", None, "+1 (202) 555-0123", "matched"),
        ("Bea Example", "bea@example.com", "+12025550123", "matched"),
        ("Ada Example", None, None, "matched"),
        ("Bea Example", None, None, "new"),
        ("Ada Stranger", None, None, "new"),
        ("Example", None, None, "new"),
    ],
)
def test_customer_identity_signals(
    business: uuid.UUID, name: str, email: str | None, phone: str | None, expected: str
) -> None:
    ctx = read_context(business)
    with transaction(ctx) as db:
        customer = crm.create_customer(
            db,
            ctx,
            crm.CustomerCreate(
                display_name="Ada Example",
                contact_points=[
                    crm.ContactPointIn(type="email", value="ada@example.com"),
                    crm.ContactPointIn(type="phone", value="+12025550123"),
                ],
            ),
        )
    with transaction(ctx) as db:
        for display_name in ("Ben Example", "Dan Example"):
            crm.create_customer(db, ctx, crm.CustomerCreate(display_name=display_name))
    _, message_id = inbound(business, visitor_name=name, visitor_email=email, visitor_phone=phone)
    with transaction(ctx) as db:
        message = conversations.get_message_context(db, ctx, message_id)
    match = intake._match_customer(message, business)
    assert match.kind == expected
    assert match.customer_id == (customer.id if expected == "matched" else None)
