"""Thread ownership and deterministic visitor hand-over acknowledgements. No inference here."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.identity import public as identity
from app.modules.org import public as org
from app.platform import audit, clock, events
from app.platform.context import Context
from app.platform.errors import Conflict
from app.platform.events import DomainEventPayload

from . import permissions as P
from .models import Conversation
from .repository import get_conversation, lock_for_customer


class HandoffRequested(DomainEventPayload):
    event_type = "conversations.handoff_requested"
    conversation_id: uuid.UUID
    reason: str
    ai_generation: int = 0


class AiOwnershipChanged(DomainEventPayload):
    event_type = "conversations.ai_ownership_changed"
    conversation_id: uuid.UUID
    ai_generation: int


def pause(db: Session, ctx: Context, conv: Conversation) -> None:
    # Every manual reply/takeover invalidates drafts prepared since the previous staff action,
    # including staff-requested AI drafts while the thread was already in human mode.
    # request_handoff checks its episode idempotency before calling this function.
    conv.ai_mode, conv.ai_paused_at = "human", clock.now()
    conv.ai_generation += 1
    conv.version += 1
    db.flush()
    approvals.invalidate_ai_replies(db, ctx, conv.id, conv.ai_generation)
    events.publish(db, ctx, AiOwnershipChanged(conversation_id=conv.id, ai_generation=conv.ai_generation))
    audit.record(
        db,
        ctx,
        action="conversations.ai_paused",
        target_type="conversation",
        target_id=conv.id,
        summary="Conversation handed to a person; AI replies paused",
    )


def _acknowledgement(db: Session, ctx: Context) -> str:
    business = org.get_business_brief(db, ctx)
    now = clock.to_zone(clock.now(), business.timezone)
    local_time = now.time().replace(tzinfo=None)
    for h in org.get_opening_hours(db, ctx):
        if h.weekday == now.weekday() and h.opens <= local_time < h.closes:
            return "A person will reply here."
    return org.get_settings(db, ctx).handoff_after_hours_text


def request_handoff(db: Session, ctx: Context, conversation_id: uuid.UUID, reason: str) -> None:
    from app.modules.notifications import public as notifications

    from .service import _add

    conv = lock_for_customer(db, ctx, conversation_id)
    if conv.handoff_requested_at is not None:
        return
    pause(db, ctx, conv)
    conv.handoff_requested_at = clock.now()
    _add(db, ctx, conv, direction="outbound", author_kind="system", body=_acknowledgement(db, ctx))
    conv.status = "waiting_on_staff"
    staff = (
        [conv.assigned_to]
        if conv.assigned_to
        else [u.id for u in identity.list_users_with_permission(db, ctx, P.PROPOSAL_APPROVE)]
    )
    notifications.notify_users(
        db,
        ctx,
        staff,
        kind="handoff",
        title="A conversation needs a person",
        body=reason[:300],
        link=f"/inbox/{conv.id}",
    )
    events.publish(
        db, ctx, HandoffRequested(conversation_id=conv.id, reason=reason[:300], ai_generation=conv.ai_generation)
    )


def take_over(db: Session, ctx: Context, conversation_id: uuid.UUID) -> Conversation:
    ctx.require(P.THREAD_WRITE)
    conv = lock_for_customer(db, ctx, conversation_id)
    pause(db, ctx, conv)
    if ctx.user_id is not None:
        conv.assigned_to = ctx.user_id
    db.flush()
    return conv


def hand_back(db: Session, ctx: Context, conversation_id: uuid.UUID) -> Conversation:
    ctx.require(P.THREAD_WRITE)
    conv = lock_for_customer(db, ctx, conversation_id)
    if conv.ai_mode == "auto":
        return conv
    conv.ai_mode, conv.ai_paused_at, conv.handoff_requested_at = "auto", None, None
    conv.ai_generation += 1
    conv.version += 1
    db.flush()
    audit.record(
        db,
        ctx,
        action="conversations.ai_resumed",
        target_type="conversation",
        target_id=conv.id,
        summary="Conversation handed back to AI for new visitor messages",
    )
    return conv


def ensure_ai_active(
    db: Session,
    ctx: Context,
    conversation_id: uuid.UUID,
    generation: int | None = None,
    *,
    lock: bool = False,
    staff_requested: bool = False,
) -> int:
    ctx.require(P.THREAD_READ)
    conv = lock_for_customer(db, ctx, conversation_id) if lock else get_conversation(db, ctx, conversation_id)
    if (conv.ai_mode != "auto" and not staff_requested) or (
        generation is not None and conv.ai_generation != generation
    ):
        raise Conflict("conversations.ai_paused", detail="A person took over this conversation; discard the AI reply.")
    return conv.ai_generation
