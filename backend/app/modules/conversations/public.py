"""Public API of the conversations module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.platform.context import Context

from . import service
from .intake import limit_public_creation, require_public_intake
from .ownership import ensure_ai_active as ensure_ai_active
from .ownership import hand_back as hand_back
from .ownership import take_over as take_over
from .publication import queue_items as publication_queue_items
from .schemas import Citation as Citation
from .schemas import GroundingResult as GroundingResult
from .schemas import MessageContext, MessageOut, ThreadDto
from .service import HandoffRequested, MessageReceived, lock_for_customer
from .visitors import issue_visitor_token, set_visitor_cookie


def retention_eligible_conversations(ctx: Context) -> Select[uuid.UUID]:
    """Scoped ID subquery for derived-data cleanup; persistent retention holds survive profile switches."""
    from .models import Conversation

    if ctx.business_id is None:
        raise ValueError("Conversation retention requires a business")
    return select(Conversation.id).where(
        Conversation.business_id == ctx.business_id, Conversation.restricted_at.is_(None)
    )


def get_thread(db: Session, ctx: Context, conversation_id: uuid.UUID, last_n: int = 20) -> ThreadDto:
    return service.get_thread(db, ctx, conversation_id, last_n)


def get_message_context(db: Session, ctx: Context, message_id: uuid.UUID) -> MessageContext:
    return service.get_message_context(db, ctx, message_id)


def has_internal_note_marker(
    db: Session,
    ctx: Context,
    conversation_id: uuid.UUID,
    marker: str,
) -> bool:
    """Scoped existence lookup using the thread index; never materializes message history.

    Accepts historical notes as well as newly written ones. Marker matching escapes SQL LIKE metacharacters.
    """
    from sqlalchemy import select

    from app.platform.crud import require_scoped

    from .models import Conversation, Message

    ctx.require("conversations.thread.read")
    require_scoped(db, ctx, Conversation, conversation_id, "conversations.conversation_not_found")
    return (
        db.scalar(
            select(Message.id)
            .where(
                Message.business_id == ctx.business_id,
                Message.conversation_id == conversation_id,
                Message.direction == "internal",
                Message.author_kind == "assistant",
                Message.body.endswith(marker, autoescape=True),
            )
            .limit(1)
        )
        is not None
    )


def send_reply(
    db: Session, ctx: Context, conversation_id: uuid.UUID, body: str, *, proposal_id: uuid.UUID | None = None
) -> MessageOut:
    return MessageOut.model_validate(service.send_reply(db, ctx, conversation_id, body, proposal_id=proposal_id))


def send_care_reply(
    db: Session,
    ctx: Context,
    conversation_id: uuid.UUID,
    body: str,
    *,
    source_message_id: uuid.UUID | None,
    not_before: datetime,
    proposal_id: uuid.UUID,
    ai_generation: int | None,
) -> MessageOut:
    """Executor-only care response bound to the approved proposal's ownership generation."""
    from app.platform.errors import Conflict

    from . import replies

    conv = lock_for_customer(db, ctx, conversation_id)
    if ai_generation is None or conv.ai_generation != ai_generation:
        raise Conflict("conversations.ai_paused")
    return MessageOut.model_validate(
        replies.send_reply(
            db,
            ctx,
            conversation_id,
            body,
            proposal_id=proposal_id,
            ai_reply=True,
            answers_message_id=source_message_id,
            ai_generation=ai_generation,
            not_before=not_before,
        )
    )


def start_conversation(
    db: Session,
    ctx: Context,
    *,
    channel: str,
    body: str,
    author_kind: str,
    customer_id: uuid.UUID | None = None,
    subject: str | None = None,
    visitor_name: str | None = None,
    visitor_email: str | None = None,
    visitor_phone: str | None = None,
    consent_to_contact: bool = False,
) -> uuid.UUID:
    conv, _ = service.start_conversation(
        db,
        ctx,
        channel=channel,
        body=body,
        author_kind=author_kind,
        customer_id=customer_id,
        subject=subject,
        visitor_name=visitor_name,
        visitor_email=visitor_email,
        visitor_phone=visitor_phone,
        consent_to_contact=consent_to_contact,
    )
    return conv.id


def link_customer(db: Session, ctx: Context, conversation_id: uuid.UUID, customer_id: uuid.UUID | None) -> None:
    service.link_customer(db, ctx, conversation_id, customer_id)


def request_handoff(db: Session, ctx: Context, conversation_id: uuid.UUID, reason: str) -> None:
    service.request_handoff(db, ctx, conversation_id, reason)


def add_internal_note(db: Session, ctx: Context, conversation_id: uuid.UUID, body: str, author_kind: str) -> MessageOut:
    return MessageOut.model_validate(service.add_internal_note(db, ctx, conversation_id, body, author_kind))


def count_open_by_status(db: Session, ctx: Context) -> dict[str, int]:
    return service.counts(db, ctx)


__all__ = [
    "Citation",
    "GroundingResult",
    "HandoffRequested",
    "MessageContext",
    "MessageOut",
    "MessageReceived",
    "ThreadDto",
    "add_internal_note",
    "count_open_by_status",
    "ensure_ai_active",
    "get_message_context",
    "get_thread",
    "hand_back",
    "has_internal_note_marker",
    "issue_visitor_token",
    "limit_public_creation",
    "link_customer",
    "lock_for_customer",
    "publication_queue_items",
    "request_handoff",
    "require_public_intake",
    "retention_eligible_conversations",
    "send_reply",
    "set_visitor_cookie",
    "start_conversation",
    "take_over",
]
