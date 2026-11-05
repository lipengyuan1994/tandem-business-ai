"""A1: due-time claim, stop/claim races, origin selection and delivery projections."""

from __future__ import annotations

import dataclasses
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from typing import Any

import pytest
from sqlalchemy import select

from app.modules.approvals import engine
from app.modules.notifications import adapters, outbound, service
from app.modules.notifications.models import OutboxMessage
from app.modules.org.models import BusinessSettingsRow
from app.platform import clock, jobs
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.registry import Registry
from tests.integration.test_a1_chat_confirm import confirm, order, proposal
from tests.integration.test_a1_chat_confirm import shop as shop


def queued(shop: Any, *, delay: int = 120) -> uuid.UUID:
    with transaction(shop["ctx"]) as db:
        settings = db.scalars(select(BusinessSettingsRow)).one()
        settings.settings = {**settings.settings, "outbound_send_delay_seconds": delay}
    order(shop, sent=True)
    p = proposal(shop, order(shop))
    assert confirm(shop, p).status_code == 200
    assert engine.execute(shop["business"], p["id"], p["revision_id"]) == "succeeded"
    with transaction(shop["ctx"]) as db:
        return db.scalars(select(OutboxMessage.id)).one()


def test_not_before_respected_by_direct_claim_and_scheduled_job(shop: Any, registry: Registry) -> None:
    ident = queued(shop)
    assert service.deliver(shop["business"], ident) == "skipped"
    jobs.run_pending(registry)
    with transaction(shop["ctx"]) as db:
        row = db.get(OutboxMessage, ident)
        assert row.status == "pending" and row.attempts == 0
    now = clock.now()
    clock.set_clock(lambda: now + timedelta(seconds=120))
    jobs.run_pending(registry)
    with transaction(shop["ctx"]) as db:
        assert db.get(OutboxMessage, ident).status == "delivered"


def test_stop_never_sends_and_audits_delivery_stopped(shop: Any, owner_engine: Any) -> None:
    ident = queued(shop)
    result = shop["client"].post(f"/api/v1/notifications/outbox/{ident}/stop")
    assert result.status_code == 200 and result.json()["status"] == "cancelled"
    now = clock.now()
    clock.set_clock(lambda: now + timedelta(seconds=1000))
    assert service.deliver(shop["business"], ident) == "skipped"
    with transaction(shop["ctx"]) as db:
        row = db.get(OutboxMessage, ident)
        assert row.attempts == 0
        pid = row.proposal_id
        assert outbound.delivery_state(db, shop["ctx"], pid) == "stopped"
    detail = shop["client"].get(f"/api/v1/approvals/proposals/{pid}").json()
    assert detail["status"] == "succeeded" and detail["delivery_state"] == "stopped"
    from sqlalchemy import text

    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from audit_events where action='notifications.outbox.stopped'")) == 1
    assert shop["client"].post(f"/api/v1/notifications/outbox/{ident}/stop").status_code == 200


@pytest.mark.parametrize("iteration", range(5))
def test_stop_vs_claim_race_exactly_one_outcome(shop: Any, monkeypatch: Any, iteration: int) -> None:
    ident = queued(shop, delay=0)
    barrier = Barrier(2)
    delivered: list[str] = []

    class Adapter:
        def send(self, message, key):
            delivered.append(key)
            return adapters.Delivered("ok")

    monkeypatch.setitem(service.ADAPTERS, "simulated", Adapter())

    def stop():
        barrier.wait(timeout=5)
        try:
            with transaction(shop["ctx"]) as db:
                outbound.stop(db, shop["ctx"], ident)
            return "stopped"
        except DomainError as exc:
            return exc.code

    def deliver():
        barrier.wait(timeout=5)
        return service.deliver(shop["business"], ident)

    with ThreadPoolExecutor(max_workers=2) as pool:
        stopped = pool.submit(stop)
        claimed = pool.submit(deliver)
        result = stopped.result(timeout=10), claimed.result(timeout=10)
    with transaction(shop["ctx"]) as db:
        row = db.get(OutboxMessage, ident)
        assert (row.status, len(delivered), result) in {
            ("cancelled", 0, ("stopped", "skipped")),
            ("delivered", 1, ("notifications.too_late_to_stop", "delivered")),
        }


def test_stop_after_claim_sending_fails(shop: Any, monkeypatch: Any) -> None:
    ident = queued(shop, delay=0)
    entered, release = Event(), Event()

    class Adapter:
        def send(self, message, key):
            entered.set()
            assert release.wait(5)
            return adapters.Delivered("ok")

    monkeypatch.setitem(service.ADAPTERS, "simulated", Adapter())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.deliver, shop["business"], ident)
        try:
            assert entered.wait(5)
            result = shop["client"].post(f"/api/v1/notifications/outbox/{ident}/stop")
            assert result.status_code == 409 and result.json()["code"] == "notifications.too_late_to_stop"
        finally:
            release.set()
        assert future.result(timeout=5) == "delivered"


@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", "scheduled"),
        ("sending", "sending"),
        ("delivered", "delivered"),
        ("failed", "failed"),
        ("unknown", "unknown"),
        ("cancelled", "stopped"),
    ],
)
def test_delivery_state_mapping(shop: Any, status: str, expected: str) -> None:
    ident = queued(shop)
    with transaction(shop["ctx"]) as db:
        row = db.get(OutboxMessage, ident)
        row.status = status
        db.flush()
        assert outbound.delivery_state(db, shop["ctx"], row.proposal_id) == expected
        assert outbound.delivery_state(db, shop["ctx"], uuid.uuid4()) == "none"


def test_staff_direct_mail_no_delay_and_ai_origin_uses_delay_even_with_human_decider(shop: Any) -> None:
    ctx = shop["ctx"]
    with transaction(ctx) as db:
        ident = service.enqueue_message(
            db,
            ctx,
            channel="simulated",
            recipient="staff@example.com",
            template_key="test_message",
            variables={"name": "Staff"},
            idempotency_key="manual",
        )
        assert db.get(OutboxMessage, ident).not_before is None
    order(shop, sent=True)
    p = proposal(shop, order(shop))
    # Queue context is human
    # Proposal origin determines the delay.
    with transaction(ctx) as db:
        ident = service.enqueue_message(
            db,
            dataclasses.replace(ctx, proposal_id=p["id"]),
            channel="simulated",
            recipient="parts@example.com",
            template_key="test_message",
            variables={"name": "Example"},
            idempotency_key="ai-human",
        )
        assert db.get(OutboxMessage, ident).not_before == clock.now() + timedelta(seconds=120)


def test_stop_authorization_action_approver_and_cross_business(shop: Any, registry: Registry) -> None:
    from app.modules.identity.testing import login_as
    from app.modules.org.testing import make_business

    ident = queued(shop)
    denied = login_as(shop["app"], shop["business"], ["operator"])
    assert denied.post(f"/api/v1/notifications/outbox/{ident}/stop").status_code == 403
    foreign = login_as(shop["app"], make_business(slug="foreign"), ["owner"])
    assert foreign.post(f"/api/v1/notifications/outbox/{ident}/stop").status_code == 404
    # Non-admin current action approve permission alone is enough.
    ctx = dataclasses.replace(shop["ctx"], permissions=frozenset({"purchasing.po.approve"}))
    with transaction(ctx) as db:
        assert outbound.stop(db, ctx, ident).status == "cancelled"


def test_claim_waits_for_stop_transaction_that_rolls_back(shop: Any, monkeypatch: Any) -> None:
    """A rejected/rolled-back stop must not consume the delivery job as a skip."""
    ident = queued(shop, delay=0)
    locked, release = Event(), Event()
    original = service._status_event

    def fail_stop(db, ctx, row):
        if row.status == "cancelled":
            locked.set()
            assert release.wait(5)
            raise RuntimeError("synthetic rollback")
        original(db, ctx, row)

    monkeypatch.setattr(service, "_status_event", fail_stop)

    def rollback_stop():
        with pytest.raises(RuntimeError), transaction(shop["ctx"]) as db:
            outbound.stop(db, shop["ctx"], ident)

    with ThreadPoolExecutor(max_workers=2) as pool:
        stopped = pool.submit(rollback_stop)
        assert locked.wait(5)
        delivered = pool.submit(service.deliver, shop["business"], ident)
        try:
            # A row lock is held until release. The targeted claim must wait, not return 'skipped'.
            from concurrent.futures import TimeoutError

            with pytest.raises(TimeoutError):
                delivered.result(timeout=0.15)
        finally:
            release.set()
        stopped.result(timeout=5)
        assert delivered.result(timeout=5) == "delivered"
