"""Delayed AI mail, atomic stop, and scoped operational projections of the RLS-exempt outbox."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.org import public as org
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.errors import Conflict, Forbidden, NotFound
from app.platform.pagination import decode_cursor
from app.platform.registry import DeliveryState, get_registry

from .models import OutboxMessage


def send_after(db: Session, ctx: Context, proposal_id: uuid.UUID | None, channel: str) -> datetime | None:
    if proposal_id is None or channel not in {"email", "simulated"}:
        return None
    state = approvals.outbound_authority(db, ctx, proposal_id)
    if state is None or state["origin_kind"] != "assistant":
        return None
    delay = org.get_settings(db, ctx).outbound_send_delay_seconds
    return clock.now() + timedelta(seconds=delay)


def stop(db: Session, ctx: Context, outbox_id: uuid.UUID) -> OutboxMessage:
    from .service import _status_event

    row = db.scalar(
        select(OutboxMessage)
        .where(OutboxMessage.business_id == ctx.business_id, OutboxMessage.id == outbox_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("notifications.not_found")
    state = approvals.outbound_authority(db, ctx, row.proposal_id) if row.proposal_id else None
    spec = get_registry().executors.get(state["action_type"]) if state else None
    if not (ctx.has("org.settings.write") or (spec is not None and ctx.has(spec.approve_permission))):
        raise Forbidden("notifications.not_authorized_to_stop")
    if row.status == "cancelled":
        return row
    if row.status != "pending":
        raise Conflict("notifications.too_late_to_stop", current_status=row.status)
    row.status = "cancelled"
    audit.record(
        db,
        ctx,
        action="notifications.outbox.stopped",
        target_type="outbox_message",
        target_id=row.id,
        summary="Outbound delivery stopped",
        proposal_id=row.proposal_id,
    )
    _status_event(db, ctx, row)
    db.flush()
    return row


def delivery_state(db: Session, ctx: Context, proposal_id: uuid.UUID) -> DeliveryState:
    statuses = set(
        db.scalars(
            select(OutboxMessage.status).where(
                OutboxMessage.business_id == ctx.business_id, OutboxMessage.proposal_id == proposal_id
            )
        )
    )
    mapping: tuple[tuple[str, DeliveryState], ...] = (
        ("unknown", "unknown"),
        ("failed", "failed"),
        ("sending", "sending"),
        ("pending", "scheduled"),
        ("cancelled", "stopped"),
        ("delivered", "delivered"),
    )
    return next((state for status, state in mapping if status in statuses), "none")


def queue_items(
    db: Session, ctx: Context, *, statuses: tuple[str, ...], limit: int, cursor: str | None = None
) -> list[dict[str, Any]]:
    ctx.require("approvals.proposal.read")
    stmt = select(OutboxMessage).where(
        OutboxMessage.business_id == ctx.business_id,
        OutboxMessage.status.in_(statuses),
    )
    if statuses == ("pending",):
        stmt = stmt.where(OutboxMessage.channel.in_(("email", "simulated")))
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(
            or_(OutboxMessage.created_at < ts, and_(OutboxMessage.created_at == ts, OutboxMessage.id < rid))
        )
    rows = db.scalars(stmt.order_by(OutboxMessage.created_at.desc(), OutboxMessage.id.desc()).limit(limit))
    return [
        {
            "id": m.id,
            "kind": "outbound_mail",
            "title": f"{m.template_key} delivery",
            "proposal_id": m.proposal_id,
            "created_at": m.created_at,
            "status": m.status,
            "stop_until": max(m.not_before or m.next_attempt_at, m.next_attempt_at) if m.status == "pending" else None,
            "failure_code": "notifications.delivery_unknown"
            if m.status == "unknown"
            else "notifications.delivery_failed"
            if m.status == "failed"
            else None,
        }
        for m in rows
    ]
