"""Concurrency and rollback regressions for shared request accounting."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from app.platform import idempotency, ratelimit
from app.platform.db import transaction
from app.platform.errors import Conflict, Invalid, RateLimited
from app.platform.ids import new_id
from app.platform.testing.auth import ctx_for


@pytest.mark.concurrency
@pytest.mark.parametrize("changed_body", [False, True])
def test_first_use_idempotency_serializes_creation(business: UUID, owner_engine: Engine, changed_body: bool) -> None:
    ctx = ctx_for(business)
    looked_up, second_looked_up = Event(), Event()

    def create(index: int) -> str:
        body = {"name": "changed" if changed_body and index else "original"}
        try:
            with transaction(ctx) as db:
                cached = idempotency.lookup(db, ctx, "shared-new-key", body)
                if index == 0:
                    looked_up.set()
                    second_looked_up.wait(1)
                else:
                    second_looked_up.set()
                if cached:
                    return str(cached.body["id"])
                item_id = str(new_id())
                db.execute(
                    text("insert into test_items(id,business_id,name) values(:id,:b,'once')"),
                    {"id": item_id, "b": business},
                )
                idempotency.store(db, ctx, "shared-new-key", body, 201, {"id": item_id})
                return item_id
        except Conflict as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create, 0)
        assert looked_up.wait(5)
        second = pool.submit(create, 1)
        a, b = first.result(timeout=10), second.result(timeout=10)
    assert b == ("platform.idempotency_key_reused" if changed_body else a)
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from test_items")).scalar_one() == 1


def test_rate_limit_survives_downstream_rollback_and_rejection(business: UUID, owner_engine: Engine) -> None:
    ctx = ctx_for(business)
    for _ in range(3):
        with pytest.raises(Invalid):
            with transaction(ctx) as db:
                ratelimit.hit(db, "invalid-request", limit=3, window_seconds=3600)
                raise Invalid("test.invalid_request")
    for _ in range(2):
        with pytest.raises(RateLimited):
            with transaction(ctx) as db:
                ratelimit.hit(db, "invalid-request", limit=3, window_seconds=3600)
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count from rate_limits where key='invalid-request'")).scalar_one() == 5
