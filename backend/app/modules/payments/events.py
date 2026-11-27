"""Stripe events forwarded by the relay: checked, stored once, then applied by a job.

The relay signs each forwarded event with this business's relay secret. The envelope names the business; the
signature proves it came from the relay client registered for that business. Events are stored once by Stripe
event id and applied by `payments.apply_event`, which re-reads the object through the relay (Stripe does not
guarantee order, and the envelope never carries the object). Account events refresh the connection; Checkout
Session events bring the payment request up to date (paylinks.apply); refund and dispute events update the card
refund (refunds.apply) or dispute (disputes.apply) they are about; payout events update the payout (payouts.apply).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.platform import clock, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, Unauthenticated

from . import connection, disputes, paylinks, payouts, refunds, signing
from .crypto import UnsealError, unseal
from .models import PaymentEvent
from .schemas import RelayEventIn

JOB_KIND = "payments.apply_event"
JOB_PERMISSIONS = paylinks.JOB_PERMISSIONS
ACCOUNT_EVENTS = frozenset(
    {"account.updated", "account.application.authorized", "capability.updated", "person.updated"}
)
DEAUTHORIZED = "account.application.deauthorized"
REPLAY_WINDOW = timedelta(days=2)


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
        event_type, object_id, account_id = row.event_type, row.object_id, row.account_id
    if event_type == DEAUTHORIZED:
        _deauthorized(ctx, row_id, account_id)
        return
    if event_type in ACCOUNT_EVENTS:
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
    if object_id and event_type in paylinks.CHECKOUT_EVENTS | refunds.EVENTS | disputes.EVENTS | payouts.EVENTS:
        try:
            outcome = _apply_object(ctx, event_type, object_id)
        except Conflict as exc:
            _finish(ctx, row_id, "failed", exc.detail or exc.code)
            if exc.code == "payments.relay_unavailable":
                raise
            return
        if outcome == refunds.WAITING:
            _finish(ctx, row_id, "failed", refunds.WAITING_NOTE)
            # Retried with back-off; replay_job also runs it again once the payment is recorded.
            raise Conflict("payments.payment_not_recorded", detail=refunds.WAITING_NOTE)
        _finish(ctx, row_id, outcome)
        return
    _finish(ctx, row_id, "ignored")


def replay_job(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    """A payment was just recorded: apply again the refund and dispute events that were waiting for it. Only recent
    ones: a Stripe page lives at most a day, so an older waiting event is about a payment made outside Tandem."""
    since = clock.now() - REPLAY_WINDOW
    waiting = db.execute(
        select(PaymentEvent.id, PaymentEvent.stripe_event_id).where(
            PaymentEvent.business_id == ctx.business_id,
            PaymentEvent.outcome == "failed",
            PaymentEvent.last_error == refunds.WAITING_NOTE,
            PaymentEvent.received_at >= since,
            PaymentEvent.event_type.in_(refunds.EVENTS | disputes.EVENTS),
        )
    ).all()
    for row_id, event_id in waiting:
        jobs.enqueue(
            db,
            JOB_KIND,
            {"event_row_id": str(row_id)},
            business_id=ctx.business_id,
            dedupe_key=f"payments-event:{ctx.business_id}:{event_id}:after:{payload['request_id']}",
        )


def _deauthorized(ctx: Context, row_id: uuid.UUID, account_id: str | None) -> None:
    """The account left the platform. The event alone does not turn payments off: the account is read back, and
    only when Stripe refuses that read is the connection marked disconnected. A stale event for an account that is
    reachable again only refreshes it."""
    try:
        connection.refresh(ctx)
    except Conflict as exc:
        if exc.code == "payments.relay_unavailable":
            _finish(ctx, row_id, "failed", exc.detail)
            raise  # the job runner retries with back-off
        if exc.code == "payments.stripe_refused" and connection.deauthorized(ctx, account_id):
            _finish(ctx, row_id, "applied")
            return
        _finish(ctx, row_id, "failed", exc.detail or exc.code)
        return
    _finish(ctx, row_id, "applied")


def _apply_object(ctx: Context, event_type: str, object_id: str) -> str:
    if event_type in paylinks.CHECKOUT_EVENTS:
        return paylinks.apply(ctx, object_id, event_type)
    if event_type in refunds.EVENTS:
        return refunds.apply(ctx, object_id)
    if event_type in payouts.EVENTS:
        return payouts.apply(ctx, object_id)
    return disputes.apply(ctx, object_id)
