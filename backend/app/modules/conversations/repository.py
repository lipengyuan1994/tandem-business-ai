"""Scoped conversation reads; mutation locks must refresh cached ORM instances after waiting."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform.context import Context
from app.platform.errors import Conflict, NotFound

from .models import Conversation, Message
from .schemas import MessageContext


def previews(db: Session, ctx: Context, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    stmt = (
        select(Message.conversation_id, Message.body)
        .where(
            Message.business_id == ctx.business_id, Message.conversation_id.in_(ids), Message.direction != "internal"
        )
        .order_by(Message.conversation_id, Message.created_at.desc())
    )
    out: dict[uuid.UUID, str] = {}
    for cid, body in db.execute(stmt):
        out.setdefault(cid, body[:140])
    return out


def counts(db: Session, ctx: Context) -> dict[str, int]:
    rows = db.execute(
        select(Conversation.status, func.count())
        .where(Conversation.business_id == ctx.business_id)
        .group_by(Conversation.status)
    )
    return {s: int(n) for s, n in rows}


def get_conversation(
    db: Session, ctx: Context, conversation_id: uuid.UUID, *, for_update: bool = False
) -> Conversation:
    stmt = select(Conversation).where(Conversation.business_id == ctx.business_id, Conversation.id == conversation_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("conversations.not_found")
    return row


def lock_for_customer(
    db: Session, ctx: Context, conversation_id: uuid.UUID, customer_id: uuid.UUID | None = None
) -> Conversation:
    observed = get_conversation(db, ctx, conversation_id)
    original_customer = observed.customer_id
    crm.lock_customers(db, ctx, {i for i in (original_customer, customer_id) if i is not None})
    conv = get_conversation(db, ctx, conversation_id, for_update=True)
    if conv.customer_id != original_customer:
        if conv.restricted_at is not None:
            raise Conflict("conversations.restricted")
        raise Conflict("conversations.version_conflict", current_version=conv.version)
    return conv


def public_messages(
    db: Session, ctx: Context, conversation_id: uuid.UUID, *, cursor: str | None = None
) -> tuple[list[Message], str | None]:
    """At most 50 visible messages after the opaque (timestamp, id) cursor, oldest first."""

    from app.platform.pagination import decode_cursor, encode_cursor

    stmt = select(Message).where(
        Message.business_id == ctx.business_id,
        Message.conversation_id == conversation_id,
        Message.direction != "internal",
        Message.publication_state == "published",
    )
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(tuple_(func.coalesce(Message.published_at, Message.created_at), Message.id) > tuple_(ts, rid))
    rows = list(
        db.scalars(stmt.order_by(func.coalesce(Message.published_at, Message.created_at), Message.id).limit(50))
    )
    return rows, encode_cursor(rows[-1].published_at or rows[-1].created_at, rows[-1].id) if rows else cursor


def reply_blockers(
    db: Session, ctx: Context, conversation_id: uuid.UUID, generation: int | None, answers: uuid.UUID | None
) -> list[str]:
    conv = lock_for_customer(db, ctx, conversation_id)
    reasons = []
    if conv.ai_mode != "auto":
        reasons.append("human_mode")
    if conv.status == "closed":
        reasons.append("conversation_closed")
    if conv.restricted_at is not None or conv.erased_at is not None:
        reasons.append("conversation_restricted")
    source = db.scalar(
        select(Message).where(
            Message.business_id == ctx.business_id,
            Message.conversation_id == conversation_id,
            Message.id == answers,
            Message.direction == "inbound",
            Message.author_kind.in_(("visitor", "customer")),
        )
    )
    latest = db.scalar(
        select(Message.id)
        .where(
            Message.business_id == ctx.business_id,
            Message.conversation_id == conversation_id,
            Message.direction == "inbound",
            Message.author_kind.in_(("visitor", "customer")),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if source is None or latest != answers or generation != conv.ai_generation:
        reasons.append("stale_reply")
    return reasons


def get_message_context(db: Session, ctx: Context, message_id: uuid.UUID) -> MessageContext:
    ctx.require("conversations.thread.read")
    row = db.execute(
        select(Message, Conversation)
        .join(
            Conversation,
            (Conversation.id == Message.conversation_id) & (Conversation.business_id == Message.business_id),
        )
        .where(
            Message.business_id == ctx.business_id,
            Conversation.business_id == ctx.business_id,
            Message.id == message_id,
        )
    ).one_or_none()
    if row is None:
        raise NotFound("conversations.message_not_found")
    message, conversation = row
    return MessageContext(
        conversation_id=conversation.id,
        channel=conversation.channel,
        author_kind=message.author_kind,
        body=message.body,
        customer_id=conversation.customer_id,
        visitor_name=conversation.visitor_name,
        visitor_email=conversation.visitor_email,
        visitor_phone=conversation.visitor_phone,
        created_at=message.created_at,
        emergency=conversation.emergency,
        ai_mode=conversation.ai_mode,
        ai_generation=conversation.ai_generation,
        message_ai_generation=message.ai_generation,
    )
