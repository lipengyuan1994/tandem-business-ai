"""Scoped conversation reads; mutation locks must refresh cached ORM instances after waiting."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform.context import Context
from app.platform.errors import Conflict, NotFound

from .models import Conversation, Message


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
    from sqlalchemy import tuple_

    from app.platform.pagination import decode_cursor, encode_cursor

    stmt = select(Message).where(
        Message.business_id == ctx.business_id,
        Message.conversation_id == conversation_id,
        Message.direction != "internal",
    )
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Message.created_at, Message.id) > tuple_(ts, rid))
    rows = list(db.scalars(stmt.order_by(Message.created_at, Message.id).limit(50)))
    return rows, encode_cursor(rows[-1].created_at, rows[-1].id) if rows else cursor
