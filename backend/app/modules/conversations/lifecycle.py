"""Conversations: export, and erasure of messages and visitor details tied to one person (M18)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform import clock
from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.errors import Invalid
from app.platform.export import dump_models
from app.platform.registry import ErasureTarget

from .models import Conversation, Message, VisitorToken


def export(db: Session, ctx: Context) -> dict[str, list[dict[str, Any]]]:
    """Visitor tokens are credentials, not data, and are not exported."""
    return dump_models(db, ctx, [Conversation, Message])


def _conversation_ids(db: Session, ctx: Context, target: ErasureTarget) -> list[uuid.UUID]:
    """Linked to the customer, or started by a visitor with one of their e-mail addresses or phone numbers."""
    emails = {e.lower() for e in target.emails}
    phones = set(target.phones_normalized)
    match = or_(Conversation.customer_id == target.customer_id, func.lower(Conversation.visitor_email).in_(emails))
    ids = set(db.scalars(select(Conversation.id).where(Conversation.business_id == ctx.business_id, match)))
    if phones:
        for cid, phone in db.execute(
            select(Conversation.id, Conversation.visitor_phone).where(
                Conversation.business_id == ctx.business_id, Conversation.visitor_phone.is_not(None)
            )
        ):
            try:
                if phone and any(_same_phone(crm.normalize_contact("phone", phone), p) for p in phones):
                    ids.add(cid)
            except Invalid:
                continue
    return sorted(ids)


def _same_phone(a: str, b: str) -> bool:
    """Equal, or equal apart from a country code that one of them was written without (+1 303 … vs 303 …)."""
    x, y = sorted((a.lstrip("+"), b.lstrip("+")), key=len)
    return x == y or (len(x) >= 7 and 1 <= len(y) - len(x) <= 3 and y.endswith(x))


def links(db: Session, ctx: Context, target: ErasureTarget) -> set[uuid.UUID]:
    """Conversations, their messages, and the outbox rows those messages were sent through."""
    ids = _conversation_ids(db, ctx, target)
    if not ids:
        return set()
    rows = db.execute(
        select(Message.id, Message.outbox_message_id).where(
            Message.business_id == ctx.business_id, Message.conversation_id.in_(ids)
        )
    ).all()
    return {*ids, *(r.id for r in rows), *(r.outbox_message_id for r in rows if r.outbox_message_id)}


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Conversations keep their ids and timestamps; message text and visitor details are removed."""
    ids = _conversation_ids(db, ctx, target)
    if not ids:
        return {"conversations_anonymised": 0, "messages_erased": 0}
    list(
        db.scalars(
            select(Conversation)
            .where(Conversation.business_id == ctx.business_id, Conversation.id.in_(ids))
            .order_by(Conversation.id)
            .with_for_update()
        )
    )
    messages = db.execute(
        update(Message)
        .where(Message.business_id == ctx.business_id, Message.conversation_id.in_(ids), Message.body != "[erased]")
        .values(body="[erased]")
    )
    conversations = db.execute(
        update(Conversation)
        .where(Conversation.business_id == ctx.business_id, Conversation.id.in_(ids))
        .values(
            subject=None,
            visitor_name=None,
            visitor_email=None,
            visitor_phone=None,
            contact_consent_at=None,
            contact_consent_text=None,
            erased_at=clock.now(),
        )
    )
    db.execute(
        update(VisitorToken)
        .where(VisitorToken.business_id == ctx.business_id, VisitorToken.conversation_id.in_(ids))
        .values(revoked_at=clock.now())
    )
    return {"conversations_anonymised": rows_affected(conversations), "messages_erased": rows_affected(messages)}


def restrict(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    ids = _conversation_ids(db, ctx, target)
    # Link matching visitor-only threads too, so clearing visitor contacts cannot bypass the restriction.
    result = db.execute(
        update(Conversation)
        .where(
            Conversation.business_id == ctx.business_id,
            or_(
                Conversation.id.in_(ids),
                Conversation.id.in_(target.linked_ids),
            ),
        )
        .values(
            customer_id=target.customer_id,
            visitor_email=None,
            visitor_phone=None,
            restricted_at=func.coalesce(Conversation.restricted_at, clock.now()),
        )
    )
    db.execute(
        update(VisitorToken)
        .where(
            VisitorToken.business_id == ctx.business_id,
            or_(
                VisitorToken.conversation_id.in_(ids),
                VisitorToken.conversation_id.in_(target.linked_ids),
            ),
        )
        .values(revoked_at=clock.now())
    )
    return {"conversations_restricted": rows_affected(result)}
