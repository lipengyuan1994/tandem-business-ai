from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text

from app.platform import audit, clock, jobs, retention
from app.platform.context import system_context
from app.platform.db import system_transaction, transaction
from app.platform.ids import new_id
from app.platform.models import DomainEvent, EventDelivery, Job
from app.platform.testing.auth import ctx_for


@pytest.mark.parametrize("dedupe_key", ["invoice:synthetic", ""])
def test_job_cleanup_is_bounded_and_preserves_deduplication(
    business: UUID, owner_engine: Engine, dedupe_key: str
) -> None:
    old = clock.now() - timedelta(days=31)
    with system_transaction() as db:
        original = new_id()
        db.add(
            Job(
                id=original,
                kind="mail.send",
                business_id=business,
                dedupe_key=dedupe_key,
                status="succeeded",
                updated_at=old,
                created_at=old,
            )
        )
        for status, date in (
            ("succeeded", old),
            ("dead", old),
            ("queued", old),
            ("running", old),
            ("succeeded", clock.now()),
        ):
            db.add(Job(kind="test.retention", status=status, updated_at=date, created_at=old))
    with transaction(ctx_for(business)) as db:
        audit.record(db, ctx_for(business), action="test.keep", target_type="test", target_id=None, summary="Keep")
    retention.cleanup(system_context(None), {})
    with system_transaction() as db:
        assert db.get(Job, original) is None
        assert jobs.enqueue(db, "mail.send", business_id=business, dedupe_key=dedupe_key) == original
        assert set(db.scalars(select(Job.status))) == {"queued", "running", "succeeded"}
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from audit_events")) == 1
        assert conn.scalar(text("select count(*) from job_dedupe")) == 1
    # More than a batch: precisely one batch is removed in one transaction.
    with owner_engine.begin() as conn:
        conn.execute(
            text("""insert into jobs(id, kind, status, updated_at)
            select gen_random_uuid(), 'test.bulk', 'dead', :old from generate_series(1, :n)"""),
            {"old": old, "n": retention.BATCH + 3},
        )
    with system_transaction() as db:
        retention.cleanup_jobs(db)
    with system_transaction() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "test.bulk")) == 3


def test_only_fully_delivered_old_events_are_removed(business: UUID) -> None:
    old = clock.now() - timedelta(days=31)
    keep = []
    remove = []
    with system_transaction() as db:
        for statuses, occurred in (
            (["done"], old),
            ([], old),
            (["done", "pending"], old),
            (["dead"], old),
            (["running"], old),
            (["done"], clock.now()),
        ):
            event = DomainEvent(
                id=new_id(),
                business_id=business,
                type="test.event",
                payload={},
                actor_kind="system",
                occurred_at=occurred,
            )
            db.add(event)
            db.flush()
            (remove if (not statuses or statuses == ["done"]) and occurred == old else keep).append(event.id)
            for index, status in enumerate(statuses):
                db.add(
                    EventDelivery(
                        event_id=event.id,
                        subscriber=str(index),
                        status=status,
                        done_at=old if status == "done" else None,
                    )
                )
        recent = DomainEvent(
            id=new_id(),
            business_id=business,
            type="test.recent_delivery",
            payload={},
            actor_kind="system",
            occurred_at=old,
        )
        db.add(recent)
        db.flush()
        db.add(EventDelivery(event_id=recent.id, subscriber="x", status="done", done_at=clock.now()))
        keep.append(recent.id)
    retention.cleanup(system_context(None), {})
    with system_transaction() as db:
        assert set(db.scalars(select(DomainEvent.id))) == set(keep)
        assert not set(remove) & set(db.scalars(select(EventDelivery.event_id)))


def test_claim_lanes_exclude_ai_from_operational_capacity() -> None:
    with system_transaction() as db:
        ai = jobs.enqueue(db, "ai.slow", priority=1)
        mail = jobs.enqueue(db, "notifications.send", priority=100)
    operational = jobs.claim("ops", lane="non_ai")
    assert operational is not None and operational["id"] == mail
    assert jobs.claim("ops", lane="non_ai") is None
    claimed_ai = jobs.claim("ai", lane="ai")
    assert claimed_ai is not None and claimed_ai["id"] == ai


def test_full_batch_schedules_one_continuation(owner_engine: Engine) -> None:
    old = clock.now() - timedelta(days=3)
    with owner_engine.begin() as conn:
        conn.execute(
            text("""insert into rate_limits(key, window_start, count)
            select 'bulk-' || n::text, :old, 1 from generate_series(1, :n) n"""),
            {"old": old, "n": retention.BATCH + 1},
        )
    retention.cleanup(system_context(None), {})
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from rate_limits")) == 1
        row = conn.execute(text("select kind, run_at, priority from jobs")).one()
        assert row.kind == "platform.retention" and row.priority == 200
        assert row.run_at > clock.now()


def test_cleanup_indexes_and_definer_privileges(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        indexes = set(conn.scalars(text("select indexname from pg_indexes where schemaname='public'")))
        assert {
            "ix_jobs_retention",
            "ix_domain_events_retention",
            "ix_rate_limits_retention",
            "ix_idempotency_keys_retention",
            "ix_sessions_retention",
            "ix_oidc_login_states_retention",
        } <= indexes
        row = conn.execute(
            text("""select p.proconfig, p.prosecdef, r.rolname
            from pg_proc p join pg_roles r on p.proowner=r.oid
            where p.oid='public.cleanup_expired_idempotency()'::regprocedure""")
        ).one()
        assert row.prosecdef and row.rolname != "smb_app"
        assert row.proconfig == ["search_path=pg_catalog, public, pg_temp"]
        assert (
            conn.scalar(
                text("""select count(*) from pg_proc p, lateral aclexplode(p.proacl) a
            where p.oid='public.cleanup_expired_idempotency()'::regprocedure and a.grantee=0""")
            )
            == 0
        )


def test_cleanup_does_not_wait_for_locked_rows(owner_engine: Engine) -> None:
    old = clock.now() - timedelta(days=31)
    with owner_engine.begin() as conn:
        job_id = new_id()
        conn.execute(
            text("insert into jobs(id, kind, status, updated_at) values (:id, 'test.locked', 'dead', :old)"),
            {"id": job_id, "old": old},
        )
    with owner_engine.begin() as locked:
        locked.execute(text("select id from jobs where id=:id for update"), {"id": job_id})
        with system_transaction() as db:
            assert retention.cleanup_jobs(db) == 0
    with system_transaction() as db:
        assert retention.cleanup_jobs(db) == 1


def test_dedupe_cleanup_respects_concurrent_producer_lock(owner_engine: Engine) -> None:
    old = clock.now() - timedelta(days=31)
    with owner_engine.begin() as conn:
        job_id = new_id()
        conn.execute(
            text("""insert into jobs(id, kind, status, updated_at, dedupe_key)
            values (:id, 'test.locked', 'dead', :old, 'test:producer')"""),
            {"id": job_id, "old": old},
        )
    with system_transaction() as producer:
        producer.execute(text("select pg_advisory_xact_lock(hashtextextended('job:test:producer', 0))"))
        with system_transaction() as db:
            retention.cleanup_jobs(db)
        assert jobs.enqueue(producer, "test.locked", dedupe_key="test:producer") == job_id
    with system_transaction() as db:
        assert db.get(Job, job_id) is not None
        retention.cleanup_jobs(db)
    with system_transaction() as db:
        assert db.get(Job, job_id) is None
        assert jobs.enqueue(db, "test.locked", dedupe_key="test:producer") == job_id
