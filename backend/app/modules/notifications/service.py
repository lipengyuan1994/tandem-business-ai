"""Outbox delivery with honest states (07-workflow-engine.md#outbox-m10-notifications) and in-app notifications.

Delivery uses three short transactions so a crash can never cause a silent duplicate send:
  A) pending -> sending (committed)   B) call the adapter (no transaction held)   C) record the result.
A message stuck in 'sending' (process died during B) becomes 'unknown' and waits for a human decision.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform import audit, clock, events, jobs
from app.platform.config import get_settings
from app.platform.context import Context, inherit_workflow, system_context, workflow_context
from app.platform.db import system_transaction, transaction
from app.platform.errors import Conflict, Forbidden, NotFound
from app.platform.ids import new_id

from . import permissions as P
from .adapters import ADAPTERS, Delivered, PermanentError, RenderedMessage, RetryableError, Unknown
from .events import DeliveryStatusChanged
from .in_app import (
    list_mine as list_mine,
)
from .in_app import (
    mark_read as mark_read,
)
from .in_app import (
    notify_users as notify_users,
)
from .in_app import (
    on_proposal_created as on_proposal_created,
)
from .in_app import (
    unread_count as unread_count,
)
from .models import DeliveryAttempt, OutboxMessage
from .outbound import send_after
from .templates import render, render_html

log = structlog.get_logger(__name__)
BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2)]
MAX_ATTEMPTS = 5
SENDING_LEASE = timedelta(minutes=5)


# --------------------------------------------------------------------------------------------------- enqueue
# Set by `simulated_delivery()`. A ContextVar, not a module flag: it follows the code that entered the block (its
# thread or task), so a worker thread or a request handler running at the same time still gets the normal rule.
_simulated_delivery: ContextVar[bool] = ContextVar("notifications_simulated_delivery", default=False)


@contextmanager
def simulated_delivery() -> Iterator[None]:
    """Inside the block `email_channel()` answers `simulated` whatever SMTP_URL says.

    For code that queues e-mail to addresses nobody owns: the demo seed writes supplier requests to example.com
    addresses, and a real provider would bounce them and damage the sender's reputation. The rows are created
    `simulated` from the start (the worker marks them delivered, the screens label them Simulated); nothing is
    rewritten afterwards. The previous state is restored when the block ends, also when it ends with an error, and
    nested blocks are fine. Code that asks for `channel="email"` explicitly (the System "Send test e-mail") is not
    affected: it is meant to prove real delivery.
    """
    token = _simulated_delivery.set(True)
    try:
        yield
    finally:
        _simulated_delivery.reset(token)


def email_channel() -> str:
    """Outbox channel for an ordinary e-mail: `simulated` until SMTP is configured, then the real `email` adapter.

    The one rule behind runbooks/email.md ("an empty SMTP_URL leaves normal notifications simulated") and M12 rule 6.
    Callers pass the result as `channel=` for the customer, supplier and campaign messages they queue. Without SMTP the
    simulated adapter marks the message delivered and the UI labels it Simulated, so nothing is sent and nothing shows
    up as a delivery issue. Features that are only meaningful with real mail (the System "Send test e-mail") do not
    use this: they pass `channel="email"` and refuse to run until SMTP is configured. Inside `simulated_delivery()`
    the answer is always `simulated`.
    """
    if _simulated_delivery.get():
        return "simulated"
    return "email" if get_settings().smtp_url else "simulated"


def enqueue_message(
    db: Session,
    ctx: Context,
    *,
    channel: str,
    recipient: str | None,
    template_key: str,
    variables: dict[str, Any],
    idempotency_key: str,
    related_type: str | None = None,
    related_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert into the outbox inside the caller's business transaction. Same idempotency key -> same message."""
    render(template_key, variables)  # fail fast on unknown templates or missing variables
    existing = db.scalar(select(OutboxMessage.id).where(OutboxMessage.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    assert ctx.business_id is not None
    not_before = send_after(db, ctx, proposal_id or ctx.proposal_id, channel)
    message = OutboxMessage(
        id=new_id(),
        business_id=ctx.business_id,
        channel=channel,
        recipient=recipient,
        template_key=template_key,
        variables=variables,
        idempotency_key=idempotency_key,
        related_type=related_type,
        related_id=related_id,
        proposal_id=proposal_id or ctx.proposal_id,
        status="pending",
        next_attempt_at=clock.now(),
        not_before=not_before,
    )
    db.add(message)
    db.flush()
    jobs.enqueue(
        db,
        "notifications.deliver",
        {"outbox_id": str(message.id)},
        business_id=ctx.business_id,
        dedupe_key=f"deliver:{message.id}:0",
        run_at=not_before,
        priority=20,
    )
    return message.id


# --------------------------------------------------------------------------------------------------- deliver
def _status_event(db: Session, ctx: Context, m: OutboxMessage) -> None:
    ctx = _workflow_context(db, ctx, m)
    events.publish(
        db,
        ctx,
        DeliveryStatusChanged(
            outbox_message_id=m.id, related_type=m.related_type, related_id=m.related_id, status=m.status
        ),
    )


def _workflow_context(db: Session, ctx: Context, m: OutboxMessage) -> Context:
    if m.proposal_id is not None:
        from dataclasses import replace

        from app.modules.approvals import public as approvals

        source = approvals.outbound_authority(db, ctx, m.proposal_id)
        if source is not None and source["correlation_id"] is not None:
            ctx = replace(
                ctx,
                correlation_id=source["correlation_id"],
                causation_id=m.proposal_id,
                agent_key=source["agent_key"],
                chain_depth=source["chain_depth"] or 0,
            )
    return ctx


def deliver(business_id: uuid.UUID, outbox_id: uuid.UUID) -> str:
    ctx = inherit_workflow(system_context(business_id), workflow_context())
    with transaction(ctx) as db:  # A: claim
        m = db.scalar(
            select(OutboxMessage)
            .where(
                OutboxMessage.business_id == business_id,
                OutboxMessage.id == outbox_id,
                OutboxMessage.status == "pending",
                OutboxMessage.restricted_at.is_(None),
                OutboxMessage.next_attempt_at <= clock.now(),
                or_(OutboxMessage.not_before.is_(None), OutboxMessage.not_before <= clock.now()),
            )
            .with_for_update()
        )
        if m is None:
            return "skipped"
        ctx = _workflow_context(db, ctx, m)
        m.status, m.attempts, m.locked_until = "sending", m.attempts + 1, clock.now() + SENDING_LEASE
        subject, body = render(m.template_key, m.variables)
        rendered = RenderedMessage(
            channel=m.channel,
            recipient=m.recipient,
            subject=subject,
            body=body,
            html=render_html(m.template_key, m.variables) if m.channel == "email" else None,
            reply_to=org.get_public_email(db, ctx) if m.channel == "email" else None,
            unsubscribe_url=m.variables.get("unsubscribe_url") if m.template_key == "marketing_campaign" else None,
        )
        key, channel, attempts, started = m.idempotency_key, m.channel, m.attempts, clock.now()
    result = ADAPTERS[channel].send(rendered, key)  # B: no transaction held during the external call
    with transaction(ctx) as db:  # C: record
        m = db.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.business_id == business_id, OutboxMessage.id == outbox_id)
            .with_for_update()
        )
        assert m is not None
        m.locked_until = None
        if isinstance(result, Delivered):
            m.status, m.provider_message_id, m.delivered_at, outcome = (
                "delivered",
                result.provider_message_id,
                clock.now(),
                "delivered",
            )
        elif isinstance(result, RetryableError) and m.restricted_at is not None:
            m.status, m.last_error, outcome = "failed", "customer restricted; delivery blocked", "blocked"
        elif isinstance(result, RetryableError) and attempts < MAX_ATTEMPTS:
            m.status, m.last_error, outcome = "pending", result.error, "retry"
            m.next_attempt_at = clock.now() + BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
            jobs.enqueue(
                db,
                "notifications.deliver",
                {"outbox_id": str(m.id)},
                business_id=business_id,
                run_at=m.next_attempt_at,
                dedupe_key=f"deliver:{m.id}:{attempts}",
                priority=20,
                ctx=ctx,
            )
        elif isinstance(result, Unknown):
            m.status, m.last_error, outcome = "unknown", result.error, "unknown"
        else:
            error = result.error if isinstance(result, PermanentError | RetryableError) else "failed"
            m.status, m.last_error, outcome = "failed", error, "failed"
        db.add(
            DeliveryAttempt(
                business_id=business_id,
                outbox_message_id=m.id,
                started_at=started,
                finished_at=clock.now(),
                result=outcome,
                error=m.last_error if outcome != "delivered" else None,
            )
        )
        if outcome != "retry":
            _status_event(db, ctx, m)
    return outcome


def job_deliver(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    assert ctx.business_id is not None
    deliver(ctx.business_id, uuid.UUID(payload["outbox_id"]))


def job_reap_sending(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    """Cron: messages stuck in 'sending' (worker died during the external call) become 'unknown'."""
    with system_transaction() as sys_db:
        stuck = list(
            sys_db.execute(
                select(OutboxMessage.id, OutboxMessage.business_id).where(
                    OutboxMessage.status == "sending", OutboxMessage.locked_until < clock.now()
                )
            )
        )
    for outbox_id, business_id in stuck:
        bctx = system_context(business_id)
        with transaction(bctx) as bdb:
            m = bdb.scalar(
                select(OutboxMessage)
                .where(OutboxMessage.id == outbox_id, OutboxMessage.status == "sending")
                .with_for_update()
            )
            if m is None:
                continue
            m.status, m.locked_until, m.last_error = "unknown", None, "worker stopped during delivery; outcome unknown"
            _status_event(bdb, bctx, m)
            log.warning("outbox_marked_unknown", outbox_id=str(outbox_id))


def resolve(db: Session, ctx: Context, outbox_id: uuid.UUID, resolution: str) -> OutboxMessage:
    """Staff decide an 'unknown' delivery: 'delivered' (confirmed out of band) or 'retry' (send again)."""
    ctx.require(P.OUTBOX_RESOLVE)
    m = db.scalar(
        select(OutboxMessage)
        .where(OutboxMessage.business_id == ctx.business_id, OutboxMessage.id == outbox_id)
        .with_for_update()
    )
    if m is None:
        raise NotFound("notifications.not_found")
    if resolution == "retry" and m.restricted_at is not None:
        raise Conflict("notifications.delivery_blocked", detail="Customer restricted; delivery cannot be retried")
    if m.status != "unknown":
        raise Conflict("notifications.not_unknown_state", current_status=m.status)
    m.resolved_by, m.resolution = ctx.user_id, resolution
    if resolution == "delivered":
        m.status, m.delivered_at = "delivered", clock.now()
    else:
        m.status, m.next_attempt_at = "pending", clock.now()
        jobs.enqueue(
            db,
            "notifications.deliver",
            {"outbox_id": str(m.id)},
            business_id=ctx.business_id,
            dedupe_key=f"deliver:{m.id}:manual:{m.attempts}",
            priority=20,
            ctx=_workflow_context(db, ctx, m),
        )
    audit.record(
        db,
        ctx,
        action=f"notifications.outbox.resolved_{resolution}",
        target_type="outbox_message",
        target_id=m.id,
        summary=f"Uncertain delivery resolved: {resolution}",
        proposal_id=m.proposal_id,
    )
    if resolution == "delivered":
        _status_event(db, ctx, m)
    return m


def list_outbox(
    db: Session, ctx: Context, *, status: str | None, proposal_id: uuid.UUID | None, limit: int = 100
) -> list[OutboxMessage]:
    if proposal_id is None:
        ctx.require(P.OUTBOX_READ)
    elif not (ctx.has(P.OUTBOX_READ) or ctx.has("approvals.proposal.read")):
        raise Forbidden("auth.permission_denied")
    stmt = select(OutboxMessage).where(OutboxMessage.business_id == ctx.business_id)
    if status:
        stmt = stmt.where(OutboxMessage.status == status)
    if proposal_id:
        stmt = stmt.where(OutboxMessage.proposal_id == proposal_id)
    return list(db.scalars(stmt.order_by(OutboxMessage.created_at.desc()).limit(min(limit, 200))))


def status_counts(db: Session, ctx: Context) -> dict[str, int]:
    rows = db.execute(
        select(OutboxMessage.status, func.count())
        .where(OutboxMessage.business_id == ctx.business_id)
        .group_by(OutboxMessage.status)
    )
    return {status: int(count) for status, count in rows}
