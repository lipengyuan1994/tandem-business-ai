"""P1-07 / P1-08: jobs, worker primitives, cron, reaper, domain events."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text, update
from sqlalchemy.orm import Session

from app.platform import clock, events, jobs
from app.platform.context import Context
from app.platform.db import system_transaction, transaction
from app.platform.events import DomainEventPayload
from app.platform.ids import new_id
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for


class ThingHappened(DomainEventPayload):
    event_type = "test.thing_happened"
    thing_id: UUID


CALLS: list[str] = []


def _insert_item(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    db.execute(
        text("insert into test_items (id, business_id, name) values (:i, :b, :n)"),
        {"i": new_id(), "b": ctx.business_id, "n": payload["name"]},
    )


def _always_fails(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    raise ValueError("boom")


def _sub_ok(db: Session, ctx: Context, payload: dict[str, Any], event_id: UUID) -> None:
    CALLS.append(f"ok:{payload['thing_id']}")


def _sub_flaky(db: Session, ctx: Context, payload: dict[str, Any], event_id: UUID) -> None:
    CALLS.append("flaky")
    if CALLS.count("flaky") == 1:
        raise RuntimeError("first attempt fails")


def _hooks(reg: Registry) -> None:
    reg.add_job("test.insert_item", _insert_item)
    reg.add_job("test.fail", _always_fails)
    reg.add_cron("test-every-minute", "test.insert_item", 60)
    reg.subscribe("test.thing_happened", "test.sub_ok", _sub_ok)
    reg.subscribe("test.thing_happened", "test.sub_flaky", _sub_flaky)


@pytest.fixture
def reg(registry_builder: Any) -> Registry:
    CALLS.clear()
    return registry_builder(_hooks)  # type: ignore[no-any-return]


def _jobs(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(text("select kind, status, attempts, run_at, dedupe_key from jobs order by created_at"))
        )


def test_handler_runs_in_business_transaction(reg: Registry, business: Any, owner_engine: Engine) -> None:
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        jobs.enqueue(db, "test.insert_item", {"name": "from job"}, business_id=business)
    assert jobs.run_pending(reg) == 1
    with owner_engine.connect() as conn:
        assert conn.execute(text("select business_id from test_items")).scalar() == business
    assert _jobs(owner_engine)[0].status == "succeeded"


def test_enqueue_with_dedupe_key_is_idempotent(reg: Registry, business: Any, owner_engine: Engine) -> None:
    with transaction(ctx_for(business)) as db:
        first = jobs.enqueue(db, "test.insert_item", {"name": "a"}, business_id=business, dedupe_key="k1")
        second = jobs.enqueue(db, "test.insert_item", {"name": "b"}, business_id=business, dedupe_key="k1")
    assert first == second and len(_jobs(owner_engine)) == 1


@pytest.mark.concurrency
def test_job_claim_is_exclusive_under_concurrency(reg: Registry, business: Any) -> None:
    with transaction(ctx_for(business)) as db:
        jobs.enqueue(db, "test.insert_item", {"name": "once"}, business_id=business)
    claimed: list[Any] = []
    barrier = threading.Barrier(8)

    def worker(i: int) -> None:
        barrier.wait()
        job = jobs.claim(f"w{i}")
        if job:
            claimed.append(job)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 1


def test_job_retry_backoff_and_dead_letter(
    reg: Registry, business: Any, owner_engine: Engine, freeze_time: Any
) -> None:
    start = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    freeze_time(start)
    with transaction(ctx_for(business)) as db:
        jobs.enqueue(db, "test.fail", {}, business_id=business, max_attempts=2)
    jobs.run_pending(reg)
    job = _jobs(owner_engine)[0]
    assert job.status == "queued" and job.attempts == 1 and job.run_at > start
    freeze_time(start + timedelta(hours=2))
    jobs.run_pending(reg)
    assert _jobs(owner_engine)[0].status == "dead"


def test_unknown_job_kind_goes_dead(reg: Registry, business: Any, owner_engine: Engine) -> None:
    with transaction(ctx_for(business)) as db:
        jobs.enqueue(db, "test.nobody_handles_this", {}, business_id=business)
    jobs.run_pending(reg)
    assert _jobs(owner_engine)[0].status == "dead"


def test_reaper_requeues_stale_running_jobs(
    reg: Registry, business: Any, owner_engine: Engine, freeze_time: Any
) -> None:
    now = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    freeze_time(now)
    with transaction(ctx_for(business)) as db:
        jobs.enqueue(db, "test.insert_item", {"name": "x"}, business_id=business)
    assert jobs.claim("dying-worker") is not None  # claimed, then the worker "dies"
    freeze_time(now + timedelta(minutes=10))
    assert jobs.reap_stale() == 1
    assert _jobs(owner_engine)[0].status == "queued"
    assert jobs.run_pending(reg) == 1


def test_cron_enqueue_is_deduplicated(reg: Registry, owner_engine: Engine, freeze_time: Any) -> None:
    freeze_time(datetime(2026, 11, 5, 17, 0, 10, tzinfo=UTC))
    jobs.enqueue_cron(reg)
    jobs.enqueue_cron(reg)  # second worker in the same minute
    assert [j.kind for j in _jobs(owner_engine)].count("test.insert_item") == 1
    freeze_time(datetime(2026, 11, 5, 17, 1, 10, tzinfo=UTC))
    jobs.enqueue_cron(reg)
    assert [j.kind for j in _jobs(owner_engine)].count("test.insert_item") == 2


def test_publish_creates_delivery_per_subscriber(reg: Registry, business: Any, owner_engine: Engine) -> None:
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        events.publish(db, ctx, ThingHappened(thing_id=new_id()), registry=reg)
    with owner_engine.connect() as conn:
        subs = sorted(conn.execute(text("select subscriber from event_deliveries")).scalars())
    assert subs == ["test.sub_flaky", "test.sub_ok"]


def test_event_not_published_when_business_tx_rolls_back(reg: Registry, business: Any, owner_engine: Engine) -> None:
    ctx = ctx_for(business)
    with pytest.raises(RuntimeError):
        with transaction(ctx) as db:
            events.publish(db, ctx, ThingHappened(thing_id=new_id()), registry=reg)
            raise RuntimeError("rollback")
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from domain_events")).scalar() == 0


def test_failed_delivery_retried_independently_per_subscriber(
    reg: Registry, business: Any, owner_engine: Engine
) -> None:
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        events.publish(db, ctx, ThingHappened(thing_id=new_id()), registry=reg)
    events.deliver_pending(reg)
    assert CALLS.count("flaky") == 1 and sum(c.startswith("ok:") for c in CALLS) == 1
    clock.set_clock(lambda: datetime.now(UTC) + timedelta(hours=2))
    events.deliver_pending(reg)
    assert CALLS.count("flaky") == 2 and sum(c.startswith("ok:") for c in CALLS) == 1  # ok subscriber not re-run
    with owner_engine.connect() as conn:
        assert set(conn.execute(text("select status from event_deliveries")).scalars()) == {"done"}


def test_system_transaction_sees_no_tenant_rows(business: Any) -> None:
    with transaction(ctx_for(business)) as db:
        db.execute(
            text("insert into test_items (id, business_id, name) values (:i, :b, 'x')"), {"i": new_id(), "b": business}
        )
    with system_transaction() as db:
        assert db.execute(text("select count(*) from test_items")).scalar() == 0


def test_non_transactional_job_runs_without_open_transaction_and_records_success(
    business: UUID, owner_engine: Engine
) -> None:
    from sqlalchemy import event

    from app.platform.db import get_engine

    reg = Registry()
    active: set[Any] = set()
    calls: list[UUID | None] = []

    def began(conn: Any) -> None:
        active.add(conn)

    def ended(conn: Any) -> None:
        active.discard(conn)

    def handler(ctx: Context, payload: dict[str, Any]) -> None:
        assert not active
        calls.append(ctx.business_id)
        assert ctx.actor_kind == "system"
        with transaction(ctx) as db:
            _insert_item(db, ctx, payload)

    reg.add_job("test.external", handler, transactional=False)
    with system_transaction() as db:
        jobs.enqueue(db, "test.external", {"name": "independent transaction"}, business_id=business)
    engine = get_engine()
    event.listen(engine, "begin", began)
    event.listen(engine, "commit", ended)
    event.listen(engine, "rollback", ended)
    try:
        assert jobs.run_pending(reg) == 1
    finally:
        event.remove(engine, "begin", began)
        event.remove(engine, "commit", ended)
        event.remove(engine, "rollback", ended)
    assert calls == [business] and not active
    with owner_engine.connect() as conn:
        assert conn.execute(text("select name from test_items")).scalar_one() == "independent transaction"
        row = conn.execute(text("select status, locked_until from jobs")).one()
        assert row.status == "succeeded" and row.locked_until is None


def test_non_transactional_job_failure_retries_like_others(
    business: UUID, owner_engine: Engine, freeze_time: Any
) -> None:
    reg = Registry()
    calls: list[int] = []

    def handler(ctx: Context, payload: dict[str, Any]) -> None:
        calls.append(len(calls) + 1)
        raise RuntimeError("external operation failed")

    reg.add_job("test.external", handler, transactional=False)
    start = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    freeze_time(start)
    with system_transaction() as db:
        jobs.enqueue(db, "test.external", business_id=business, max_attempts=2)
    assert jobs.run_pending(reg) == 1
    first = _jobs(owner_engine)[0]
    assert first.status == "queued" and first.attempts == 1 and first.run_at > start
    with owner_engine.connect() as conn:
        row = conn.execute(text("select locked_by, locked_until, last_error from jobs")).one()
        assert row.locked_by is None and row.locked_until is None
        assert "external operation failed" in row.last_error
    freeze_time(start + timedelta(hours=2))
    assert jobs.run_pending(reg) == 1
    assert _jobs(owner_engine)[0].status == "dead" and calls == [1, 2]


@pytest.mark.parametrize("late_failure", [False, True])
@pytest.mark.parametrize("new_status", ["running", "dead"])
@pytest.mark.parametrize("reuse_worker", [False, True])
def test_non_transactional_job_stale_attempt_cannot_finish_or_resurrect(
    business: UUID,
    owner_engine: Engine,
    freeze_time: Any,
    late_failure: bool,
    new_status: str,
    reuse_worker: bool,
) -> None:
    reg = Registry()
    now = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    freeze_time(now)
    before: dict[str, Any] = {}

    def handler(ctx: Context, payload: dict[str, Any]) -> None:
        freeze_time(now + timedelta(minutes=6))
        assert jobs.reap_stale() == 1
        newer = jobs.claim("original" if reuse_worker else "replacement")
        assert newer is not None and newer["attempts"] == 2
        if new_status == "dead":
            freeze_time(now + timedelta(minutes=12))
            assert jobs.reap_stale() == 1
        with owner_engine.connect() as conn:
            before.update(conn.execute(text("select * from jobs")).mappings().one())
        assert before["status"] == new_status
        if late_failure:
            raise RuntimeError("old worker failed after losing its lease")

    reg.add_job("test.long_operation", handler, transactional=False)
    with system_transaction() as db:
        jobs.enqueue(db, "test.long_operation", business_id=business, max_attempts=2)
    original = jobs.claim("original")
    assert original is not None
    assert jobs.run_job(reg, original) is False
    with owner_engine.connect() as conn:
        assert dict(conn.execute(text("select * from jobs")).mappings().one()) == before


@pytest.mark.parametrize("late_failure", [False, True])
@pytest.mark.parametrize("lost_field", ["attempts", "locked_by", "status"])
def test_non_transactional_job_requires_every_claim_field(
    business: UUID,
    owner_engine: Engine,
    late_failure: bool,
    lost_field: str,
) -> None:
    from app.platform.models import Job

    reg = Registry()
    before: dict[str, Any] = {}
    changes = {"attempts": 2, "locked_by": "replacement", "status": "dead"}

    def handler(ctx: Context, payload: dict[str, Any]) -> None:
        with owner_engine.begin() as conn:
            conn.execute(update(Job).values({lost_field: changes[lost_field]}))
            before.update(conn.execute(text("select * from jobs")).mappings().one())
        if late_failure:
            raise RuntimeError("late failure")

    reg.add_job("test.claim_fence", handler, transactional=False)
    with system_transaction() as db:
        jobs.enqueue(db, "test.claim_fence", business_id=business, max_attempts=2)
    original = jobs.claim("original")
    assert original is not None
    assert jobs.run_job(reg, original) is False
    with owner_engine.connect() as conn:
        assert dict(conn.execute(text("select * from jobs")).mappings().one()) == before


def test_per_business_cron_enqueues_one_job_per_active_business(
    business: UUID, other_business: UUID, make_business: Any, owner_engine: Engine, freeze_time: Any
) -> None:
    inactive = make_business("inactive-shop")
    with owner_engine.begin() as conn:
        conn.execute(text("update businesses set status='suspended' where id=:id"), {"id": inactive})
    reg = Registry()
    reg.add_cron("business-scan", "test.scan", 60, per_business=True)
    reg.add_cron("global-scan", "test.global", 60)
    start = datetime(2026, 11, 5, 17, 0, 10, tzinfo=UTC)
    freeze_time(start)
    jobs.enqueue_cron(reg)
    jobs.enqueue_cron(reg)
    slot = int(start.timestamp()) // 60
    with owner_engine.connect() as conn:
        rows = conn.execute(text("select business_id, dedupe_key from jobs where kind='test.scan'")).all()
        assert set(rows) == {(b, f"cron:business-scan:{slot}:{b}") for b in (business, other_business)}
        assert conn.execute(text("select business_id, dedupe_key from jobs where kind='test.global'")).one() == (
            None,
            f"cron:global-scan:{slot}",
        )
    freeze_time(start + timedelta(minutes=1))
    jobs.enqueue_cron(reg)
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from jobs where kind='test.scan'")).scalar_one() == 4


def test_active_business_ids_definer_is_safe(business: UUID, owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "select p.prosecdef, p.proconfig, r.rolname from pg_proc p join pg_roles r on r.oid=p.proowner "
                "where p.oid='public.active_business_ids()'::regprocedure"
            )
        ).one()
        assert row.prosecdef and row.rolname != "smb_app"
        assert row.proconfig == ["search_path=pg_catalog, public, pg_temp"]
        assert conn.execute(
            text("select has_function_privilege('smb_app', 'public.active_business_ids()', 'execute')")
        ).scalar_one()
        assert (
            conn.execute(
                text(
                    "select count(*) from pg_proc p, lateral aclexplode(p.proacl) a "
                    "where p.oid='public.active_business_ids()'::regprocedure and a.grantee=0"
                )
            ).scalar_one()
            == 0
        )
    with system_transaction() as db:
        assert list(db.scalars(text("select * from public.active_business_ids()"))) == [business]
    # smb_app has no TEMP privilege; the owner invokes the definer with an empty temporary shadow
    # to verify that its source remains public.businesses without changing installation grants.
    with owner_engine.begin() as conn:
        conn.execute(text("create temporary table businesses (id uuid, status text) on commit drop"))
        assert list(conn.scalars(text("select * from public.active_business_ids()"))) == [business]
