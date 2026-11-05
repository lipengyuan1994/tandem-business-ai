"""Domain events with per-subscriber, at-least-once delivery (07-workflow-engine.md#domain-events).

`publish` writes the event and one delivery row per registered subscriber in the caller's transaction,
so nothing is published if the business change rolls back. Subscribers must be idempotent.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any, ClassVar
from uuid import UUID

import structlog
from pydantic import BaseModel
from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.canonical import to_jsonable
from app.platform.context import Context, system_context
from app.platform.db import system_transaction, transaction
from app.platform.ids import new_id
from app.platform.jobs import backoff
from app.platform.models import DomainEvent, EventDelivery
from app.platform.registry import Registry, get_registry

log = structlog.get_logger(__name__)
MAX_DELIVERY_ATTEMPTS = 8


class DomainEventPayload(BaseModel):
    """Base for event payload models. Subclasses set `event_type` ('<module>.<past_tense>')."""

    event_type: ClassVar[str]
    schema_version: ClassVar[int] = 1


def publish(db: Session, ctx: Context, event: DomainEventPayload, registry: Registry | None = None) -> UUID:
    if ctx.business_id is None:
        raise ValueError("events must belong to a business")
    reg = registry or get_registry()
    event_id = new_id()
    db.execute(
        insert(DomainEvent).values(
            id=event_id,
            business_id=ctx.business_id,
            type=event.event_type,
            schema_version=event.schema_version,
            payload=to_jsonable(event.model_dump(mode="json")),
            actor_kind=ctx.actor_kind,
            actor_id=ctx.user_id,
            request_id=ctx.request_id,
        )
    )
    for subscriber in sorted(reg.subscribers.get(event.event_type, {})):
        db.execute(insert(EventDelivery).values(event_id=event_id, subscriber=subscriber, run_at=clock.now()))
    return event_id


CLAIM_SQL = text(
    """
    update event_deliveries d set status = 'running', attempts = d.attempts + 1,
           locked_until = :now + interval '5 minutes'
      from domain_events e
     where e.id = d.event_id
       and (d.event_id, d.subscriber) = (
            select event_id, subscriber from event_deliveries
             where status = 'pending' and run_at <= :now
             order by run_at for update skip locked limit 1)
    returning d.event_id, d.subscriber, d.attempts, e.type, e.business_id, e.payload
    """
)


def _claim() -> dict[str, Any] | None:
    with system_transaction() as db:
        row = db.execute(CLAIM_SQL, {"now": clock.now()}).mappings().first()
        return dict(row) if row else None


def _key(d: dict[str, Any]) -> Any:
    return (EventDelivery.event_id == d["event_id"]) & (EventDelivery.subscriber == d["subscriber"])


def _claim_key(d: dict[str, Any]) -> Any:
    return _key(d) & (EventDelivery.status == "running") & (EventDelivery.attempts == d["attempts"])


def deliver(registry: Registry, d: dict[str, Any]) -> bool:
    handler = registry.subscribers.get(d["type"], {}).get(d["subscriber"])
    ctx = system_context(d["business_id"])
    try:
        with transaction(ctx) as db:
            delivery = db.scalar(select(EventDelivery).where(_key(d)).with_for_update())
            if (
                delivery is None
                or delivery.status != "running"
                or delivery.attempts != d["attempts"]
                or delivery.locked_until is None
                or delivery.locked_until <= clock.now()
            ):
                return False
            if handler is None:
                raise LookupError(f"subscriber {d['subscriber']} no longer registered for {d['type']}")
            handler(db, ctx, d["payload"], d["event_id"])
            db.execute(
                update(EventDelivery).where(_claim_key(d)).values(status="done", done_at=clock.now(), locked_until=None)
            )
        return True
    except Exception:
        dead = d["attempts"] >= MAX_DELIVERY_ATTEMPTS
        with system_transaction() as db:
            db.execute(
                update(EventDelivery)
                .where(_claim_key(d))
                .values(
                    status="dead" if dead else "pending",
                    run_at=clock.now() + backoff(d["attempts"]),
                    locked_until=None,
                    last_error=traceback.format_exc()[-4000:],
                )
            )
        log.warning("event_delivery_failed", subscriber=d["subscriber"], event_type=d["type"], dead=dead)
        return False


def deliver_pending(registry: Registry, limit: int = 100, *, on_progress: Callable[[], None] | None = None) -> int:
    done = 0
    while done < limit and (d := _claim()) is not None:
        if deliver(registry, d) and on_progress is not None:
            on_progress()
        done += 1
    return done


def reap_stale() -> int:
    with system_transaction() as db:
        result = db.execute(
            text(
                "update event_deliveries set status='pending', locked_until=null"
                " where (event_id, subscriber) in ("
                " select event_id, subscriber from event_deliveries"
                " where status='running' and locked_until < :now for update skip locked)"
            ),
            {"now": clock.now()},
        )
        return result.rowcount  # type: ignore[attr-defined, no-any-return]
