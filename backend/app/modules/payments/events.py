"""Stripe events forwarded by the relay: checked, stored once, then applied by a job.

The relay signs each forwarded event with this business's relay secret. The envelope names the business; the
signature proves it came from the relay client registered for that business. Events are stored once by Stripe
event id and applied by `payments.apply_event`, which re-reads the object through the relay (Stripe does not
guarantee order, and the envelope never carries the object). Phase 1 applies account events; payment events
follow in phase 2.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.platform import clock, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, Unauthenticated

from . import connection, signing
from . import permissions as P
from .crypto import UnsealError, unseal
from .models import PaymentEvent
from .schemas import RelayEventIn

JOB_KIND = "payments.apply_event"
JOB_PERMISSIONS = frozenset({P.CONNECTION_READ, P.CONNECTION_MANAGE, "org.business.read"})
ACCOUNT_EVENTS = frozenset(
    {"account.updated", "account.application.authorized", "capability.updated", "person.updated"}
)
DEAUTHORIZED = "account.application.deauthorized"


def receive(db: Session, ctx: Context, event: RelayEventIn, body: bytes, client_id: str, signature: str) -> bool:
    """Check and store one forwarded event. Returns False for an event already stored."""
    refused = Unauthenticated("payments.relay_signature", detail="Unknown relay client or bad signature")
    conn = connection.get(db, ctx)
    if conn is None or not conn.relay_client_id or not conn.relay_secret_sealed or conn.relay_client_id != client_id:
        raise refused
    try:
        secret = unseal(conn.relay_secret_sealed)
    except UnsealError:
        raise refused from None
    if not signing.verify(secret, signature, body, int(clock.now().timestamp())):
        raise refused
    if conn.stripe_account_id is not None and conn.stripe_account_id != event.account:
        raise Conflict("payments.account_mismatch", detail="The event is for another Stripe account")
    row_id = db.scalar(
        pg_insert(PaymentEvent)
        .values(
            id=uuid.uuid4(),
            business_id=ctx.business_id,
            stripe_event_id=event.id,
            event_type=event.type,
            account_id=event.account,
            object_type=event.object.object,
            object_id=event.object.id,
            livemode=event.livemode,
            stripe_created_at=connection.from_unix(event.created),
            received_at=clock.now(),
            outcome="pending",
        )
        .on_conflict_do_nothing(index_elements=["business_id", "stripe_event_id"])
        .returning(PaymentEvent.id)
    )
    if row_id is None:
        return False
    jobs.enqueue(
        db,
        JOB_KIND,
        {"event_row_id": str(row_id)},
        business_id=ctx.business_id,
        dedupe_key=f"payments-event:{ctx.business_id}:{event.id}",
    )
    return True


def _finish(ctx: Context, row_id: uuid.UUID, outcome: str, error: str | None = None) -> None:
    with transaction(ctx) as db:
        row = db.get(PaymentEvent, row_id, with_for_update=True)
        if row is None:
            return
        row.outcome = outcome
        row.processed_at = clock.now() if outcome != "failed" else None
        row.last_error = error[:500] if error else None
        row.version += 1


def apply_job(ctx: Context, payload: dict[str, Any]) -> None:
    """Job handler (non-transactional: it talks to the relay between short transactions)."""
    ctx = replace(ctx, permissions=JOB_PERMISSIONS)
    row_id = uuid.UUID(str(payload["event_row_id"]))
    with transaction(ctx) as db:
        row = db.scalar(select(PaymentEvent).where(PaymentEvent.id == row_id).with_for_update())
        if row is None or row.outcome in ("applied", "ignored"):
            return
        row.attempts += 1
        row.version += 1
        event_type = row.event_type
    if event_type in ACCOUNT_EVENTS or event_type == DEAUTHORIZED:
        try:
            connection.refresh(ctx)
        except Conflict as exc:
            if exc.code == "payments.relay_unavailable":
                _finish(ctx, row_id, "failed", exc.detail)
                raise  # the job runner retries with back-off
            _finish(ctx, row_id, "failed", exc.detail or exc.code)
            return
        _finish(ctx, row_id, "applied")
        return
    _finish(ctx, row_id, "ignored")
