"""Threaded C1 publication and delegation lock-order regressions."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, event, text

from app.main import create_app
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.tests.tool_helpers import Demo
from app.modules.conversations import public as conversations
from app.modules.conversations import publication
from app.modules.conversations.models import Message
from app.modules.identity.testing import login_as
from app.platform import clock
from app.platform.context import Context
from app.platform.db import get_engine, transaction
from app.platform.errors import Conflict
from app.platform.registry import Registry
from tests.integration.test_grounded_replies import draft, execute, rule
from tests.integration.test_grounded_replies import setup as setup


@pytest.mark.parametrize("iteration", range(5))
def test_stop_vs_publish_race_one_outcome(setup: tuple[Demo, Context], owner_engine: Engine, iteration: int) -> None:
    demo, ctx = setup
    rule(ctx, shadow=False)
    p = draft(demo)
    mid = execute(owner_engine, demo, p)
    with transaction(ctx) as db:
        due = db.get(Message, mid).publish_at
    clock.set_clock(lambda: due + timedelta(seconds=iteration))
    barrier = threading.Barrier(2)

    def stop() -> str:
        barrier.wait(timeout=10)
        try:
            with transaction(ctx) as db:
                return publication.stop(db, ctx, mid).publication_state
        except Conflict as exc:
            assert exc.code == "conversations.too_late_to_stop"
            return "too_late"

    def publish() -> None:
        barrier.wait(timeout=10)
        with transaction(ctx) as db:
            publication.publish(db, ctx, {"message_id": str(mid)})

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(stop), pool.submit(publish)
        stopped = a.result(timeout=20)
        b.result(timeout=20)
    with transaction(ctx) as db:
        row = db.get(Message, mid)
        assert (row.publication_state, stopped) in {("published", "too_late"), ("stopped", "stopped")}
        if stopped == "stopped":
            assert publication.stop(db, ctx, mid).publication_state == "stopped"


def test_stop_permissions_scope_idempotency_and_never_public(
    setup: tuple[Demo, Context], owner_engine: Engine, registry: Registry
) -> None:
    demo, ctx = setup
    rule(ctx, shadow=False)
    p = draft(demo)
    mid = execute(owner_engine, demo, p)
    app = create_app(registry)
    auditor = login_as(app, demo.business, ["auditor"])
    operator = login_as(app, demo.business, ["operator"])
    foreign = login_as(app, demo.other, ["owner"])
    path = f"/api/v1/conversations/messages/{mid}/stop"
    assert auditor.post(path).status_code == 403
    assert foreign.post(path).status_code == 404
    assert operator.post(path).status_code == 200
    assert operator.post(path).json()["publication_state"] == "stopped"
    with transaction(ctx) as db:
        due = db.get(Message, mid).publish_at
    clock.set_clock(lambda: due)
    with transaction(ctx) as db:
        publication.publish(db, ctx, {"message_id": str(mid)})
        assert db.get(Message, mid).publication_state == "stopped"


@pytest.mark.parametrize("stage", ["policy", "publish"])
def test_linked_customer_lock_precedes_conversation_lock(
    setup: tuple[Demo, Context], owner_engine: Engine, stage: str
) -> None:
    demo, ctx = setup
    rule(ctx, shadow=False)
    conv, msg = inbound(demo.business)
    customer = demo.customers["Ada Example"]
    with transaction(ctx) as db:
        conversations.link_customer(db, ctx, conv, customer)
    p = draft(demo, conv=conv, msg=msg)
    mid = execute(owner_engine, demo, p) if stage == "publish" else None
    if mid:
        with transaction(ctx) as db:
            due = db.get(Message, mid).publish_at
        clock.set_clock(lambda: due)
    attempted_customer = threading.Event()

    def before(*args: Any) -> None:
        statement, parameters = args[2:4]
        if "pg_advisory_xact_lock" in statement and f"booking:{demo.business}:{customer}" in str(parameters):
            attempted_customer.set()

    def action() -> None:
        if stage == "policy":
            execute(owner_engine, demo, p)
        else:
            with transaction(ctx) as db:
                publication.publish(db, ctx, {"message_id": str(mid)})

    app_engine = get_engine()
    event.listen(app_engine, "before_cursor_execute", before)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            # Represents a visitor/takeover that already owns the customer lock and next needs the thread.
            with owner_engine.begin() as holder:
                holder.execute(text("select id from customers where id=:id for update"), {"id": customer})
                pending = pool.submit(action)
                assert attempted_customer.wait(timeout=10)
                holder.execute(text("select id from conversations where id=:id for update nowait"), {"id": conv})
            pending.result(timeout=20)
    finally:
        event.remove(app_engine, "before_cursor_execute", before)
