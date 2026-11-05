"""Background jobs in PostgreSQL (ADR-006, 07-workflow-engine.md#jobs).

`enqueue` runs inside the caller's business transaction. The worker claims with FOR UPDATE SKIP LOCKED,
runs transactional handlers and records success in the same business transaction. Non-transactional handlers
run without a worker-held transaction; success is recorded in a separate short transaction.
Handlers must be idempotent: after a crash a job can run again.
"""

from __future__ import annotations

import random
import traceback
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import and_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.platform import clock
from app.platform.context import system_context
from app.platform.db import system_transaction, transaction
from app.platform.ids import new_id
from app.platform.models import Job, JobDedupe
from app.platform.registry import JobHandler, NonTransactionalJobHandler, Registry

log = structlog.get_logger(__name__)
LOCK_MINUTES = 5


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    business_id: UUID | None = None,
    run_at: datetime | None = None,
    priority: int = 100,
    dedupe_key: str | None = None,
    max_attempts: int = 8,
) -> UUID:
    """Insert a job. With an existing `dedupe_key` this is a no-op returning the existing job's id."""
    # Serialise cleanup/enqueue for a key, including its first use. Tombstones keep
    # the original id after payload retention expires; callers still get a no-op.
    if dedupe_key is not None:
        db.execute(text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"job:{dedupe_key}"})
        previous = db.scalar(select(JobDedupe.job_id).where(JobDedupe.key == dedupe_key))
        if previous is not None:
            return previous
    values: dict[str, Any] = {
        "id": new_id(),
        "kind": kind,
        "payload": payload or {},
        "business_id": business_id,
        "priority": priority,
        "dedupe_key": dedupe_key,
        "max_attempts": max_attempts,
        "run_at": run_at or clock.now(),
    }
    stmt = pg_insert(Job).values(**values).on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(Job.id)
    job_id = db.scalar(stmt)
    if job_id is None:
        job_id = db.scalar(select(Job.id).where(Job.dedupe_key == dedupe_key))
    assert job_id is not None
    return job_id


CLAIM_SQL = text(
    """
    update jobs set status = 'running', locked_by = :worker, locked_until = :now + make_interval(mins => :lock_min),
                    attempts = attempts + 1, updated_at = :now
     where id = (select id from jobs where status = 'queued' and run_at <= :now
                  and (:lane = 'all' or (:lane = 'ai' and kind like 'ai.%')
                       or (:lane = 'non_ai' and kind not like 'ai.%'))
                  order by priority, run_at for update skip locked limit 1)
    returning id, business_id, kind, payload, attempts, max_attempts, locked_by
    """
)


def backoff(attempts: int) -> timedelta:
    base = min(5 * 2**attempts, 3600)
    return timedelta(seconds=base * random.uniform(0.8, 1.2))  # noqa: S311 - jitter, not security


def claim(worker: str, *, lane: str = "all") -> dict[str, Any] | None:
    with system_transaction() as db:
        row = (
            db.execute(CLAIM_SQL, {"worker": worker, "now": clock.now(), "lock_min": LOCK_MINUTES, "lane": lane})
            .mappings()
            .first()
        )
        return dict(row) if row else None


def _claimed_attempt(job: dict[str, Any]) -> ColumnElement[bool]:
    return and_(
        Job.id == job["id"],
        Job.attempts == job["attempts"],
        Job.locked_by == job["locked_by"],
        Job.status == "running",
    )


def _fail(job: dict[str, Any], error: str, *, fenced: bool = False) -> None:
    dead = job["attempts"] >= job["max_attempts"]
    with system_transaction() as db:
        saved = db.scalar(
            update(Job)
            .where(_claimed_attempt(job) if fenced else Job.id == job["id"])
            .values(
                status="dead" if dead else "queued",
                run_at=clock.now() + backoff(job["attempts"]),
                locked_by=None,
                locked_until=None,
                last_error=error[-4000:],
            )
            .returning(Job.id)
        )
    if fenced and saved is None:
        log.info("job_claim_lost", job_id=str(job["id"]), attempts=job["attempts"])
        return
    log.warning("job_failed", job_id=str(job["id"]), kind=job["kind"], attempts=job["attempts"], dead=dead)


def run_job(registry: Registry, job: dict[str, Any]) -> bool:
    spec = registry.jobs.get(job["kind"])
    if spec is None:
        job["attempts"] = job["max_attempts"]  # unknown kinds go straight to dead
        _fail(job, f"no handler registered for {job['kind']}")
        return False
    ctx = system_context(job["business_id"])
    try:
        if spec.transactional:
            with transaction(ctx) as db:
                cast(JobHandler, spec.handler)(db, ctx, job["payload"])
                db.execute(update(Job).where(Job.id == job["id"]).values(status="succeeded", locked_until=None))
        else:
            cast(NonTransactionalJobHandler, spec.handler)(ctx, job["payload"])
            with system_transaction() as db:
                saved = db.scalar(
                    update(Job)
                    .where(_claimed_attempt(job))
                    .values(status="succeeded", locked_until=None)
                    .returning(Job.id)
                )
            if saved is None:
                log.info("job_claim_lost", job_id=str(job["id"]), attempts=job["attempts"])
                return False
    except Exception:
        _fail(job, traceback.format_exc(), fenced=not spec.transactional)
        return False
    return True


def run_pending(registry: Registry, worker: str = "inline", limit: int = 100) -> int:
    """Run ready jobs synchronously until none are left (or `limit`). Used by tests and the worker loop."""
    done = 0
    while done < limit and (job := claim(worker)) is not None:
        run_job(registry, job)
        done += 1
    return done


def reap_stale() -> int:
    """Requeue (or bury) jobs whose worker died while holding the lock."""
    now = clock.now()
    with system_transaction() as db:
        result = db.execute(
            text(
                """update jobs set status = case when attempts >= max_attempts then 'dead' else 'queued' end,
                                   locked_by = null, locked_until = null, run_at = :now
                    where status = 'running' and locked_until < :now"""
            ),
            {"now": now},
        )
        return result.rowcount  # type: ignore[attr-defined, no-any-return]


def enqueue_cron(registry: Registry) -> None:
    """Enqueue each scheduled job once per time slot (dedupe key makes this safe across workers)."""
    epoch = int(clock.now().timestamp())
    with system_transaction() as db:
        for spec in registry.cron:
            slot = epoch // spec.every_seconds
            if spec.per_business:
                for business_id in db.scalars(text("select * from public.active_business_ids()")):
                    enqueue(
                        db,
                        spec.job_kind,
                        {},
                        business_id=business_id,
                        dedupe_key=f"cron:{spec.name}:{slot}:{business_id}",
                        priority=50,
                    )
            else:
                enqueue(db, spec.job_kind, {}, dedupe_key=f"cron:{spec.name}:{slot}", priority=50)
