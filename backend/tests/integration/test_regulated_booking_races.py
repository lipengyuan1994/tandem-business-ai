"""Real PostgreSQL interleavings: either booking commits first and blocks restriction, or restriction wins."""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.modules.admin_ops import service as admin_ops
from app.modules.admin_ops.tests.test_data import CYCLE, NOW, customer
from app.modules.admin_ops.tests.test_data import demo as demo
from app.modules.crm import public as crm
from app.modules.crm.models import Customer
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.executors import BOOK
from app.modules.scheduling.schemas import BookAppointmentPayload, BookingRequest
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.ids import new_id
from app.platform.registry import ExecutionMeta

from .test_regulated import regulated as regulated


def _fixture(demo: Any, engine: Engine) -> tuple[Context, dict[str, Any], dict[str, Any]]:
    owner = demo["owner"]
    ada = customer(owner, "Ada Example")
    with engine.connect() as conn:
        service_id = str(
            conn.execute(
                text("select service_id from appointments where business_id=:b limit 1"), {"b": CYCLE}
            ).scalar_one()
        )
    slots = owner.get(
        "/api/v1/scheduling/slots",
        params={
            "service_id": service_id,
            "earliest": (NOW + timedelta(days=7)).isoformat(),
            "latest": (NOW + timedelta(days=8)).isoformat(),
        },
    )
    assert slots.status_code == 200 and slots.json(), slots.text
    slot = slots.json()[0]
    return (
        Context(
            request_id="booking-race",
            actor_kind="user",
            user_id=owner.user_id,
            business_id=CYCLE,
            permissions=owner.permissions,
        ),
        ada,
        {
            "service_id": UUID(service_id),
            "resource_id": UUID(slot["resource_id"]),
            "customer_id": UUID(ada["id"]),
            "starts_at": datetime.fromisoformat(slot["starts_at"]),
        },
    )


def _perform(db: Session, ctx: Context, method: str, values: dict[str, Any]) -> None:
    if method == "hold":
        scheduling.place_hold(db, ctx, **values)
    elif method == "executor":
        payload = BookAppointmentPayload.model_validate(
            {
                **{key: value for key, value in values.items() if key != "customer_id"},
                "customer": {"id": values["customer_id"]},
            }
        )
        BOOK.run(db, ctx, payload, ExecutionMeta(new_id(), new_id(), "booking-race"))
    else:
        scheduling.book(db, ctx, BookingRequest(**values))


def _wait_blocked(engine: Engine, waiter: int, blocker: int) -> None:
    deadline = time.monotonic() + 5
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            if conn.execute(
                text("select :blocker = any(pg_blocking_pids(:waiter))"), {"waiter": waiter, "blocker": blocker}
            ).scalar_one():
                return
            threading.Event().wait(0.01)
    raise AssertionError("The second transaction did not wait for the customer's row lock")


@pytest.mark.parametrize("method", ["book", "hold", "executor"])
def test_restriction_commits_first_and_booking_revalidates_under_lock(
    demo: Any, regulated: None, owner_engine: Engine, method: str
) -> None:
    ctx, ada, values = _fixture(demo, owner_engine)
    pids: queue.Queue[int] = queue.Queue()

    def book() -> str:
        try:
            with transaction(ctx) as db:
                # Keep a stale ORM object alive: the locked read must refresh it after waiting.
                stale = db.get(Customer, UUID(ada["id"]))
                assert stale is not None and stale.status == "active"
                pids.put(db.scalar(select(func.pg_backend_pid())))
                _perform(db, ctx, method, values)
            return "booked"
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction(ctx) as db:
            assert admin_ops.erase_customer(db, ctx, UUID(ada["id"]), "Ada Example").status == "restricted"
            blocker = db.scalar(select(func.pg_backend_pid()))
            future = pool.submit(book)
            _wait_blocked(owner_engine, pids.get(timeout=10), blocker)
        assert future.result(timeout=10) == "crm.customer_not_found"
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "select count(*) from appointments where customer_id=:c "
                    "and status in ('held','confirmed') and ends_at>:now"
                ),
                {"c": ada["id"], "now": NOW},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("method", ["book", "hold", "executor"])
def test_booking_commits_first_and_shared_erasure_checks_block_restriction(
    demo: Any, regulated: None, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    ctx, ada, values = _fixture(demo, owner_engine)
    locked, finish = threading.Event(), threading.Event()
    pids: queue.Queue[int] = queue.Queue()
    original = crm.get_booking_customer

    def paused(db: Session, call_ctx: Context, cid: UUID) -> crm.CustomerSummary:
        result = original(db, call_ctx, cid)
        pids.put(db.scalar(select(func.pg_backend_pid())))
        locked.set()
        assert finish.wait(10)
        return result

    monkeypatch.setattr(crm, "get_booking_customer", paused)

    def book() -> None:
        with transaction(ctx) as db:
            _perform(db, ctx, method, values)

    def restrict() -> tuple[str, dict[str, Any]]:
        try:
            with transaction(ctx) as db:
                pids.put(db.scalar(select(func.pg_backend_pid())))
                admin_ops.erase_customer(db, ctx, UUID(ada["id"]), "Ada Example")
            return "restricted", {}
        except DomainError as exc:
            return exc.code, exc.extra

    with ThreadPoolExecutor(max_workers=2) as pool:
        booking = pool.submit(book)
        try:
            assert locked.wait(10)
            blocker = pids.get(timeout=10)
            restriction = pool.submit(restrict)
            _wait_blocked(owner_engine, pids.get(timeout=10), blocker)
        finally:
            finish.set()
        booking.result(timeout=10)
        code, extra = restriction.result(timeout=10)
    assert code == "admin_ops.erasure_blocked"
    assert any("upcoming appointment" in reason for reason in extra["reasons"])
    with owner_engine.connect() as conn:
        assert conn.execute(text("select status from customers where id=:c"), {"c": ada["id"]}).scalar_one() == "active"
        assert (
            conn.execute(
                text("select count(*) from erasure_requests where customer_id=:c"), {"c": ada["id"]}
            ).scalar_one()
            == 0
        )
