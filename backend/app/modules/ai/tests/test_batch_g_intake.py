"""Public messages remain visible to staff when business AI admission is exhausted."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.subscribers import intake_trigger
from app.modules.ai.tests.helpers import fake_router_by_class, step_tool
from app.modules.ai.tests.test_intake import inbound, notes, read_context
from app.modules.ai.usecases import intake
from app.modules.catalog import public as catalog
from app.modules.conversations import public as conversations
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.context import system_context
from app.platform.db import transaction

pytestmark = pytest.mark.usefixtures("registry")


@pytest.fixture
def business() -> uuid.UUID:
    return make_business()


@pytest.fixture
def other_business() -> uuid.UUID:
    return make_business(slug="second-example")


def trigger(business: uuid.UUID, message: uuid.UUID) -> None:
    ctx = system_context(business)
    with transaction(ctx) as db:
        intake_trigger(db, ctx, {"message_id": str(message), "author_kind": "visitor"}, uuid.uuid4())


@pytest.mark.parametrize("limit", ["ai_public_intake_per_hour", "ai_public_intake_max_pending"])
def test_over_limit_keeps_message_hands_off_and_enqueues_no_ai(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, owner_engine: Engine, limit: str
) -> None:
    monkeypatch.setattr(get_settings(), limit, 0)
    conv, message = inbound(business)
    trigger(business, message)
    trigger(business, message)
    with transaction(read_context(business)) as db:
        stored = conversations.get_thread(db, read_context(business), conv)
    assert any(m.id == message and m.body == "Can you repair my brakes?" for m in stored.messages)
    outcomes = notes(business, conv)
    assert len(outcomes) == 1 and "No AI call was made; please handle this message." in outcomes[0]
    expected = "Hourly public AI intake limit" if limit.endswith("per_hour") else "AI intake queue is full"
    assert expected in outcomes[0]
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from jobs where kind='ai.intake'")) == 0
        assert db.scalar(text("select status from conversations where id=:id"), {"id": conv}) == "waiting_on_staff"


@pytest.mark.parametrize("limit", ["ai_public_intake_per_hour", "ai_public_intake_max_pending"])
@pytest.mark.concurrency
def test_concurrent_event_delivery_never_exceeds_per_business_limit(
    business: uuid.UUID, other_business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, owner_engine: Engine, limit: str
) -> None:
    monkeypatch.setattr(get_settings(), limit, 2)
    messages = [inbound(business) for _ in range(6)]
    start = Barrier(6)

    def deliver(pair: tuple[uuid.UUID, uuid.UUID]) -> None:
        start.wait(timeout=10)
        trigger(business, pair[1])

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(deliver, messages))
    # Retry accepted events: no extra quota charge or job. Rejected events remain one handoff note.
    for _, message in messages:
        trigger(business, message)
    trigger(other_business, inbound(other_business)[1])
    with owner_engine.connect() as db:
        assert (
            db.scalar(text("select count(*) from jobs where kind='ai.intake' and business_id=:b"), {"b": business}) == 2
        )
        assert (
            db.scalar(
                text("select count(*) from jobs where kind='ai.intake' and business_id=:b"), {"b": other_business}
            )
            == 1
        )
        assert (
            db.scalar(
                text("select count(*) from conversations where business_id=:b and handoff_requested_at is not null"),
                {"b": business},
            )
            == 4
        )


def test_pending_limit_counts_running_jobs(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, owner_engine: Engine
) -> None:
    monkeypatch.setattr(get_settings(), "ai_public_intake_max_pending", 1)
    trigger(business, inbound(business)[1])
    with owner_engine.begin() as db:
        db.execute(text("update jobs set status='running' where kind='ai.intake'"))
    conv, message = inbound(business)
    trigger(business, message)
    assert "queue is full" in notes(business, conv)[0]


def test_hourly_quota_resets_in_utc(business: uuid.UUID, freeze_time: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_public_intake_per_hour", 1)
    now = datetime(2026, 11, 4, 4, 59, tzinfo=UTC)
    freeze_time(now)
    trigger(business, inbound(business)[1])
    conv, message = inbound(business)
    trigger(business, message)
    assert "Hourly" in notes(business, conv)[0]
    freeze_time(now + timedelta(minutes=2))
    conv2, message2 = inbound(business)
    trigger(business, message2)
    assert notes(business, conv2) == []


def test_execution_quota_checks_before_safety_and_agent(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_public_intake_per_hour", 0)
    conv, message = inbound(business)
    safety, agent = FakeGateway(["unused"]), FakeGateway(["unused"])
    result = intake.run(message, business, router=fake_router_by_class(safety=safety, agent=agent))
    assert result.extra["reason"] == "public_intake_limit"
    assert not safety.calls and not agent.calls
    assert len(notes(business, conv)) == 1 and "Hourly public AI intake limit" in notes(business, conv)[0]


@pytest.mark.concurrency
def test_concurrent_execution_quota_is_atomic(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.ai.public_intake import admit_run

    monkeypatch.setattr(get_settings(), "ai_public_intake_per_hour", 2)
    messages = [inbound(business)[1] for _ in range(5)]
    start = Barrier(5)

    def attempt(message: uuid.UUID) -> bool:
        start.wait(timeout=10)
        ctx = system_context(business)
        with transaction(ctx) as db:
            return admit_run(db, ctx, message)

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(attempt, messages)) == 2


def test_denied_event_remains_handed_off_after_quota_resets(
    business: uuid.UUID, freeze_time: Any, monkeypatch: pytest.MonkeyPatch, owner_engine: Engine
) -> None:
    at = datetime(2026, 11, 4, 4, 59, tzinfo=UTC)
    freeze_time(at)
    monkeypatch.setattr(get_settings(), "ai_public_intake_per_hour", 0)
    conv, message = inbound(business)
    trigger(business, message)
    freeze_time(at + timedelta(hours=1))
    monkeypatch.setattr(get_settings(), "ai_public_intake_per_hour", 30)
    trigger(business, message)
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from jobs where kind='ai.intake'")) == 0
    assert len(notes(business, conv)) == 1


def test_denied_job_retry_after_quota_reset_still_makes_no_model_call(
    business: uuid.UUID, freeze_time: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = datetime(2026, 11, 4, 4, 59, tzinfo=UTC)
    freeze_time(at)
    monkeypatch.setattr(get_settings(), "ai_public_intake_per_hour", 0)
    conv, message = inbound(business)
    safety, agent = FakeGateway(["unused"]), FakeGateway(["unused"])
    router = fake_router_by_class(safety=safety, agent=agent)
    intake.run(message, business, router=router)
    freeze_time(at + timedelta(hours=1))
    monkeypatch.setattr(get_settings(), "ai_public_intake_per_hour", 30)
    intake.run(message, business, router=router)
    assert not safety.calls and not agent.calls
    assert len(notes(business, conv)) == 1


def test_intake_redacts_contacts_without_corrupting_numeric_uuid_suffixes(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_id = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-123456789012")
    part_id = uuid.UUID("bbbbbbbb-cccc-4ddd-8eee-987654321012")
    quantity = Decimal("123456789.000")
    service = catalog.ServiceRequirements(
        service_id=service_id,
        name="Contact +12025550123 or desk@example.com",
        duration_minutes=30,
        buffer_minutes=0,
        resource_kind="staff",
        price_minor=123456789,
        currency="USD",
        parts=[catalog.ServicePartOut(item_id=part_id, item_name="Part desk@example.com", quantity=quantity)],
    )
    monkeypatch.setattr(catalog, "list_active_service_requirements", lambda db, ctx: [service])
    conv, message = inbound(business)
    safety = FakeGateway(['{"flagged":false,"category":"none"}'])
    agent = FakeGateway(
        [step_tool("propose_reply", conversation_id=conv, text="A person will help.", request_handoff=True)]
    )
    result = intake.run(message, business, router=fake_router_by_class(safety=safety, agent=agent))
    assert result.status == "succeeded" and len(agent.calls) == 1
    context = "\n".join(m.content for m in agent.calls[0].messages)
    assert str(service_id) in context and str(part_id) in context
    assert str(quantity) in context and '"price_minor": 123456789' in context
    assert "+12025550123" not in context and "desk@example.com" not in context
    assert "[phone hidden]" in context and "[email hidden]" in context
