"""Web-chat release and Stop share conversation -> message locks and one transaction."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.org import public as org
from app.platform import audit, clock, jobs
from app.platform.context import Context
from app.platform.errors import Conflict, Forbidden, NotFound
from app.platform.pagination import decode_cursor
from app.platform.registry import DeliveryState, get_registry

from .models import Message
from .repository import get_conversation, lock_for_customer, reply_blockers


def schedule(db: Session, ctx: Context, msg: Message) -> None:
    jobs.enqueue(
        db,
        "conversations.publish_reply",
        {"message_id": str(msg.id)},
        business_id=ctx.business_id,
        run_at=msg.publish_at,
        dedupe_key=f"publish:{msg.id}",
    )


def send_time(db: Session, ctx: Context) -> datetime:
    return clock.now() + timedelta(seconds=org.get_settings(db, ctx).outbound_send_delay_seconds)


def _locked(db: Session, ctx: Context, message_id: uuid.UUID) -> Message:
    observed = db.scalar(select(Message).where(Message.business_id == ctx.business_id, Message.id == message_id))
    if observed is None:
        raise NotFound("conversations.message_not_found")
    lock_for_customer(db, ctx, observed.conversation_id)
    row = db.scalar(
        select(Message)
        .where(Message.business_id == ctx.business_id, Message.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("conversations.message_not_found")
    return row


def stop(db: Session, ctx: Context, message_id: uuid.UUID) -> Message:
    row = _locked(db, ctx, message_id)
    authority = approvals.outbound_authority(db, ctx, row.proposal_id) if row.proposal_id else None
    spec = get_registry().executors.get(authority["action_type"]) if authority else None
    if not (ctx.has("org.settings.write") or (spec is not None and ctx.has(spec.approve_permission))):
        raise Forbidden("conversations.not_authorized_to_stop")
    if row.publication_state == "stopped":
        return row
    if row.publication_state != "withheld":
        raise Conflict("conversations.too_late_to_stop", current_status=row.publication_state)
    row.publication_state, row.delivery_status, row.cancellation_reason = "stopped", "stopped", "staff_stopped"
    audit.record(
        db,
        ctx,
        action="conversations.reply_stopped",
        target_type="message",
        target_id=row.id,
        summary="Web-chat reply stopped before publication",
        proposal_id=row.proposal_id,
    )
    db.flush()
    return row


def publish(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    from .service import _timeline

    row = _locked(db, ctx, uuid.UUID(payload["message_id"]))
    if row.publication_state != "withheld":
        return
    assert row.publish_at is not None
    if clock.now() < row.publish_at:
        # An early direct/recovered attempt must leave a durable due-time job, even if this attempt completes.
        jobs.enqueue(db, "conversations.publish_reply", payload, business_id=ctx.business_id, run_at=row.publish_at)
        return
    callback = get_registry().customer_care_delivery_guard
    guard = callback(db, ctx, row.proposal_id) if callback and row.proposal_id else None
    if guard and guard.not_before and guard.not_before > clock.now() and not guard.reason:
        row.publish_at = guard.not_before
        jobs.enqueue(
            db, "conversations.publish_reply", payload, business_id=ctx.business_id, run_at=row.publish_at, ctx=ctx
        )
        return
    reasons = reply_blockers(db, ctx, row.conversation_id, row.ai_generation, row.answers_message_id)
    if guard:
        if guard.allow_human:
            reasons = [reason for reason in reasons if reason != "human_mode"]
        if guard.reason:
            reasons.insert(0, guard.reason)
        if guard.body is not None:
            row.body = guard.body
    if reasons:
        row.publication_state, row.delivery_status, row.cancellation_reason = "cancelled", "failed", reasons[0]
    else:
        row.publication_state, row.delivery_status, row.published_at = "published", "published", clock.now()
        conv = get_conversation(db, ctx, row.conversation_id)
        conv.status, conv.last_message_at = "waiting_on_customer", row.published_at
        conv.version += 1
        _timeline(db, ctx, conv, row)
    audit.record(
        db,
        ctx,
        action=f"conversations.reply_{row.publication_state}",
        target_type="message",
        target_id=row.id,
        summary=f"Web-chat reply {row.publication_state}"
        + (f": {row.cancellation_reason}" if row.cancellation_reason else ""),
        proposal_id=row.proposal_id,
    )
    db.flush()


def delivery_state(db: Session, ctx: Context, proposal_id: uuid.UUID) -> DeliveryState:
    statuses = set(
        db.scalars(
            select(Message.publication_state).where(
                Message.business_id == ctx.business_id,
                Message.proposal_id == proposal_id,
                Message.publish_at.is_not(None),
            )
        )
    )
    mapping: tuple[tuple[str, DeliveryState], ...] = (
        ("cancelled", "failed"),
        ("withheld", "withheld"),
        ("stopped", "stopped"),
        ("published", "published"),
    )
    return next(
        (state for status, state in mapping if status in statuses),
        "none",
    )


def queue_items(
    db: Session, ctx: Context, *, failed: bool = False, limit: int = 50, cursor: str | None = None
) -> list[dict[str, Any]]:
    ctx.require("approvals.proposal.read")
    stmt = select(Message).where(
        Message.business_id == ctx.business_id, Message.publication_state == ("cancelled" if failed else "withheld")
    )
    if cursor:
        from sqlalchemy import tuple_

        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Message.created_at, Message.id) < tuple_(ts, rid))
    return [
        {
            "id": m.id,
            "kind": "web_reply",
            "title": "Web-chat reply",
            "created_at": m.created_at,
            "status": m.publication_state,
            "proposal_id": m.proposal_id,
            "stop_until": m.publish_at,
            "failure_code": m.cancellation_reason,
        }
        for m in db.scalars(stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit))
    ]
