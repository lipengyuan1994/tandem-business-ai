"""A delivery claim cannot replay or overwrite work owned by another attempt."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event, current_thread
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.platform import clock, events
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.events import DomainEventPayload
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for


class FencingEvent(DomainEventPayload):
    event_type = "test.fencing"


def _publish(registry: Registry, business: UUID) -> dict[str, Any]:
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        events.publish(db, ctx, FencingEvent(), registry=registry)
    claimed = events._claim()
    assert claimed is not None
    return claimed


def test_stale_delivery_claim_cannot_invoke_subscriber(registry_builder: Any, business: UUID) -> None:
    calls: list[UUID] = []

    def handler(db: Session, ctx: Context, payload: dict[str, Any], event_id: UUID) -> None:
        calls.append(event_id)

    reg = registry_builder(lambda reg: reg.subscribe("test.fencing", "test.handler", handler))
    start = datetime(2026, 11, 5, 17, tzinfo=UTC)
    clock.set_clock(lambda: start)
    old = _publish(reg, business)
    clock.set_clock(lambda: start + timedelta(minutes=6))
    # An expired claim cannot start work even before the reaper runs.
    assert events.deliver(reg, old) is False
    assert calls == []
    assert events.reap_stale() == 1
    fresh = events._claim()
    assert fresh is not None and fresh["attempts"] == old["attempts"] + 1
    assert events.deliver(reg, fresh) is True
    assert events.deliver(reg, old) is False
    assert calls == [old["event_id"]]


@pytest.mark.concurrency
def test_running_subscriber_is_locked_against_expired_lease_replay(
    registry_builder: Any, business: UUID, owner_engine: Engine
) -> None:
    entered, release = Event(), Event()
    calls: list[UUID] = []

    def handler(db: Session, ctx: Context, payload: dict[str, Any], event_id: UUID) -> None:
        calls.append(event_id)
        entered.set()
        assert release.wait(5)

    reg = registry_builder(lambda reg: reg.subscribe("test.fencing", "test.handler", handler))
    start = datetime(2026, 11, 5, 17, tzinfo=UTC)
    clock.set_clock(lambda: start)
    claim = _publish(reg, business)
    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery = pool.submit(events.deliver, reg, claim)
        try:
            assert entered.wait(5)
            clock.set_clock(lambda: start + timedelta(minutes=6))
            # The reaper must skip a subscriber whose transaction still owns the row.
            reaped = pool.submit(events.reap_stale).result(timeout=2)
            assert reaped == 0
            assert events._claim() is None
        finally:
            release.set()
        assert delivery.result(timeout=5) is True
    assert calls == [claim["event_id"]]
    with owner_engine.connect() as conn:
        assert conn.execute(text("select status from event_deliveries")).scalar_one() == "done"


@pytest.mark.concurrency
def test_stale_failure_cannot_overwrite_newer_done_delivery(
    registry_builder: Any, business: UUID, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting, release = Event(), Event()
    calls: list[UUID] = []

    def handler(db: Session, ctx: Context, payload: dict[str, Any], event_id: UUID) -> None:
        calls.append(event_id)
        if len(calls) == 1:
            raise ValueError("first attempt fails")

    reg = registry_builder(lambda reg: reg.subscribe("test.fencing", "test.handler", handler))
    start = datetime(2026, 11, 5, 17, tzinfo=UTC)
    clock.set_clock(lambda: start)
    claim = _publish(reg, business)
    original = events.system_transaction

    @contextmanager
    def delayed_failure_transaction() -> Iterator[Session]:
        if current_thread().name.startswith("failed-delivery"):
            waiting.set()
            assert release.wait(5)
        with original() as db:
            yield db

    monkeypatch.setattr(events, "system_transaction", delayed_failure_transaction)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="failed-delivery") as pool:
        delivery = pool.submit(events.deliver, reg, claim)
        try:
            assert waiting.wait(5)
            clock.set_clock(lambda: start + timedelta(minutes=6))
            assert events.reap_stale() == 1
            newer = events._claim()
            assert newer is not None
            assert events.deliver(reg, newer) is True
        finally:
            release.set()
        assert delivery.result(timeout=5) is False
    with owner_engine.connect() as conn:
        row = conn.execute(text("select status, attempts, last_error from event_deliveries")).one()
        assert row.status == "done" and row.attempts == 2 and row.last_error is None
