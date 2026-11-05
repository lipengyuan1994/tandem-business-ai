"""Daily, bounded retention. Audit and permanent deduplication receipts are never purged."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.platform import clock, jobs
from app.platform.context import Context
from app.platform.db import system_transaction
from app.platform.models import Job, JobDedupe
from app.platform.registry import Registry

BATCH = 500


def delete_batch(db: Session, table: str, predicate: str, order: str, params: dict[str, Any]) -> int:
    """Identifiers/predicate are code constants supplied by each owning module, never user input."""
    result = db.execute(
        text(
            f"delete from {table} where ctid in (select ctid from {table} where {predicate} "  # noqa: S608
            f"order by {order} limit :limit for update skip locked)"
        ),
        {**params, "limit": BATCH},
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def cleanup_jobs(db: Session) -> int:
    rows = db.execute(
        select(Job.id, Job.dedupe_key)
        .where(Job.status.in_(("succeeded", "dead", "failed")), Job.updated_at < clock.now() - timedelta(days=30))
        .order_by(Job.updated_at)
        .limit(BATCH)
        .with_for_update(skip_locked=True)
    )
    count = 0
    for job in rows:
        count += 1
        if job.dedupe_key is not None:
            # Match enqueue's lock without waiting behind an active producer.
            locked = db.scalar(
                text("select pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"job:{job.dedupe_key}"}
            )
            if not locked:
                continue
            db.execute(pg_insert(JobDedupe).values(key=job.dedupe_key, job_id=job.id).on_conflict_do_nothing())
        db.execute(delete(Job).where(Job.id == job.id))
    return count


def cleanup(ctx: Context, payload: dict[str, Any]) -> None:
    # Separate short transactions bound locks, pool occupancy and rollback work.
    with system_transaction() as db:
        more = cleanup_jobs(db) == BATCH
    with system_transaction() as db:
        cutoff = clock.now() - timedelta(days=30)
        # Lock parents before children, as publish/claim do. Keep events with any
        # undelivered/dead subscriber, and keep recently delivered old events.
        ids = list(
            db.scalars(
                text("""
            select e.id from domain_events e where e.occurred_at < :cutoff
            and not exists (select 1 from event_deliveries d where d.event_id=e.id
                            and (d.status <> 'done' or d.done_at is null or d.done_at >= :cutoff))
            order by e.occurred_at limit :limit for update of e skip locked
        """),
                {"cutoff": cutoff, "limit": BATCH},
            )
        )
        more |= len(ids) == BATCH
        if ids:
            db.execute(text("delete from event_deliveries where event_id = any(:ids)"), {"ids": ids})
            db.execute(text("delete from domain_events where id = any(:ids)"), {"ids": ids})
    with system_transaction() as db:
        more |= (
            delete_batch(
                db, "rate_limits", "window_start < :cutoff", "window_start", {"cutoff": clock.now() - timedelta(days=2)}
            )
            == BATCH
        )
    with system_transaction() as db:
        more |= db.scalar(text("select public.cleanup_expired_idempotency()")) == BATCH
        if more:
            schedule_continuation(db, "platform.retention")


def schedule_continuation(db: Session, kind: str) -> None:
    """Yield capacity between batches; coalesce concurrent daily/continuation scans."""
    run_at = clock.now() + timedelta(minutes=1)
    jobs.enqueue(db, kind, run_at=run_at, priority=200, dedupe_key=f"retention:{kind}:{int(run_at.timestamp()) // 60}")


def register(reg: Registry) -> None:
    reg.add_job("platform.retention", cleanup, transactional=False)
    reg.add_cron("platform-retention", "platform.retention", 86400)
