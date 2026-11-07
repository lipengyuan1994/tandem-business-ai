"""M04 inbox rules: visitor isolation, message events, and outbound replies through the outbox."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform import audit, clock, crud, events
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid
from app.platform.events import DomainEventPayload
from app.platform.ids import new_id
from app.platform.registry import get_registry

from . import permissions as P
from . import publication
from .consent import CONTACT_CONSENT_TEXT, promote_contact_consent
from .delivery import on_delivery_status as on_delivery_status
from .models import Conversation, Message
from .ownership import HandoffRequested as HandoffRequested
from .ownership import request_handoff as request_handoff
from .replies import send_reply as send_reply
from .repository import counts as counts
from .repository import get_conversation as _conv
from .repository import get_message_context as get_message_context
from .repository import lock_for_customer as lock_for_customer
from .repository import previews as previews
from .repository import public_messages as public_messages
from .schemas import ConversationCreate, ConversationUpdate, MessageOut, ThreadDto
from .visitors import VISITOR_TTL as VISITOR_TTL
from .visitors import issue_visitor_token as issue_visitor_token
from .visitors import visitor_conversation as visitor_conversation


class MessageReceived(DomainEventPayload):
    event_type = "conversations.message_received"
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    channel: str
    author_kind: str
    is_first_message: bool


def _timeline(db: Session, ctx: Context, conv: Conversation, msg: Message) -> None:
    if conv.customer_id is None or msg.direction == "internal" or msg.publication_state != "published":
        return
    word = "Message from customer" if msg.direction == "inbound" else "Reply sent"
    crm.add_timeline_entry(
        db,
        ctx,
        crm.TimelineEntryIn(
            customer_id=conv.customer_id,
            occurred_at=msg.published_at or msg.created_at or clock.now(),
            kind="message",
            ref_module="conversations",
            ref_type="message",
            ref_id=msg.id,
            summary=f"{word}: {msg.body[:120]}",
        ),
    )


def _add(
    db: Session,
    ctx: Context,
    conv: Conversation,
    *,
    direction: str,
    author_kind: str,
    body: str,
    proposal_id: uuid.UUID | None = None,
    source_ref: str | None = None,
    withheld: bool = False,
    answers_message_id: uuid.UUID | None = None,
    ai_generation: int | None = None,
) -> Message:
    if len(body) > 8000:
        raise Invalid("conversations.message_too_long")
    assert ctx.business_id is not None
    first = (
        db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.business_id == ctx.business_id, Message.conversation_id == conv.id)
        )
        == 0
    )
    # Use the insertion time after the thread lock, not PostgreSQL's transaction-start timestamp:
    # a writer that waited behind another request must remain visible after that request's cursor.
    now = clock.now()
    # The thread lock, not timestamp/UUID ordering, determines freshness. Keep the first input at generation 0;
    # every subsequent visitor/customer input invalidates earlier work even if clocks tie or move backwards.
    if not first and direction == "inbound" and author_kind in {"visitor", "customer"}:
        conv.ai_generation += 1
    msg = Message(
        created_at=now,
        id=new_id(),
        business_id=ctx.business_id,
        conversation_id=conv.id,
        direction=direction,
        author_kind=author_kind,
        author_user_id=ctx.user_id if author_kind == "staff" else None,
        body=body,
        proposal_id=proposal_id,
        source_ref=source_ref,
        ai_generation=conv.ai_generation if ai_generation is None else ai_generation,
        publication_state="withheld" if withheld else "published",
        publish_at=publication.send_time(db, ctx) if withheld else None,
        answers_message_id=answers_message_id,
    )
    db.add(msg)
    if not withheld:
        conv.last_message_at = now
    if direction == "inbound":
        conv.status = "waiting_on_staff"
    elif direction == "outbound" and not withheld:
        conv.status = "waiting_on_customer"
    conv.version += 1
    db.flush()
    _timeline(db, ctx, conv, msg)
    if direction == "inbound":
        events.publish(
            db,
            ctx,
            MessageReceived(
                conversation_id=conv.id,
                message_id=msg.id,
                channel=conv.channel,
                author_kind=author_kind,
                is_first_message=first,
            ),
        )
    if direction == "inbound" and author_kind in {"visitor", "customer"}:
        from app.modules.org import public as org

        from .emergency import signals_emergency
        from .ownership import request_handoff

        settings = org.get_settings(db, ctx)
        if settings.emergency_handling and signals_emergency(body, org.get_business_brief(db, ctx).locale):
            request_handoff(db, ctx, conv.id, "Emergency signal: immediate staff attention required", emergency=True)
    return msg


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
) -> tuple[Conversation, Message]:
    assert ctx.business_id is not None
    if customer_id is not None:
        crm.lock_customers(db, ctx, {customer_id})
        crm.get_customer_summary(db, ctx, customer_id)
    conv = Conversation(
        id=new_id(),
        business_id=ctx.business_id,
        customer_id=customer_id,
        channel=channel,
        subject=subject,
        status="open",
        last_message_at=clock.now(),
        visitor_name=visitor_name,
        visitor_email=visitor_email,
        visitor_phone=visitor_phone,
        contact_consent_at=clock.now() if consent_to_contact and visitor_email else None,
        contact_consent_text=CONTACT_CONSENT_TEXT if consent_to_contact and visitor_email else None,
    )
    db.add(conv)
    db.flush()
    promote_contact_consent(db, ctx, conv)
    msg = _add(db, ctx, conv, direction="inbound", author_kind=author_kind, body=body)
    audit.record(
        db,
        ctx,
        action="conversations.started",
        target_type="conversation",
        target_id=conv.id,
        summary=f"New {channel.replace('_', ' ')} conversation",
    )
    return conv, msg


def create_by_staff(db: Session, ctx: Context, data: ConversationCreate) -> Conversation:
    ctx.require(P.THREAD_WRITE)
    conv, _ = start_conversation(
        db,
        ctx,
        channel=data.channel,
        body=data.body,
        author_kind="staff",
        customer_id=data.customer_id,
        subject=data.subject,
        visitor_name=data.visitor_name,
    )
    return conv


def update_conversation(
    db: Session, ctx: Context, conversation_id: uuid.UUID, data: ConversationUpdate
) -> Conversation:
    ctx.require(P.THREAD_WRITE)
    conv = lock_for_customer(db, ctx, conversation_id, data.customer_id)
    if conv.version != data.version:
        raise Conflict("conversations.version_conflict", current_version=conv.version)
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    if "customer_id" in changes:
        link_customer(db, ctx, conv.id, changes.pop("customer_id"))
    for k, v in changes.items():
        setattr(conv, k, v)
    conv.version += 1
    audit.record(
        db,
        ctx,
        action="conversations.updated",
        target_type="conversation",
        target_id=conv.id,
        summary=f"Conversation updated ({', '.join(sorted(changes)) or 'customer linked'})",
    )
    return conv


def link_customer(db: Session, ctx: Context, conversation_id: uuid.UUID, customer_id: uuid.UUID | None) -> None:
    conv = lock_for_customer(db, ctx, conversation_id, customer_id)
    if customer_id != conv.customer_id and (
        conv.restricted_at is not None
        or (conv.customer_id is not None and crm.is_restricted(db, ctx, conv.customer_id))
    ):
        raise Conflict(
            "conversations.restricted", detail="A restricted conversation cannot be linked to another customer"
        )
    if customer_id is not None:
        crm.get_customer_summary(db, ctx, customer_id)
    conv.customer_id = customer_id
    conv.version += 1
    db.flush()
    if customer_id is not None:
        if bind_care := get_registry().customer_care_customer_linked:
            bind_care(db, ctx, conv.id, customer_id)
        promote_contact_consent(db, ctx, conv)
        for msg in db.scalars(
            select(Message).where(Message.business_id == ctx.business_id, Message.conversation_id == conv.id)
        ):
            _timeline(db, ctx, conv, msg)
    audit.record(
        db,
        ctx,
        action="conversations.customer_linked",
        target_type="conversation",
        target_id=conv.id,
        summary="Customer linked" if customer_id else "Customer unlinked",
    )


def add_internal_note(
    db: Session, ctx: Context, conversation_id: uuid.UUID, body: str, author_kind: str = "staff"
) -> Message:
    if author_kind == "staff":
        ctx.require(P.THREAD_WRITE)
    conv = lock_for_customer(db, ctx, conversation_id)
    return _add(db, ctx, conv, direction="internal", author_kind=author_kind, body=body)


def staff_message(db: Session, ctx: Context, conversation_id: uuid.UUID, direction: str, body: str) -> Message:
    if direction == "outbound":
        return send_reply(db, ctx, conversation_id, body)
    return add_internal_note(db, ctx, conversation_id, body)


# ------------------------------------------------------------------------------------------------------- reads
def list_conversations(
    db: Session,
    ctx: Context,
    *,
    status: str | None,
    channel: str | None,
    assigned_to_me: bool,
    customer_id: uuid.UUID | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[Conversation], str | None]:
    ctx.require(P.THREAD_READ)
    stmt = select(Conversation).where(Conversation.business_id == ctx.business_id)
    if status:
        stmt = stmt.where(Conversation.status == status)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if assigned_to_me:
        stmt = stmt.where(Conversation.assigned_to == ctx.user_id)
    if customer_id:
        stmt = stmt.where(Conversation.customer_id == customer_id)
    return crud.page(db, stmt, Conversation, limit, cursor)


def messages(
    db: Session, ctx: Context, conversation_id: uuid.UUID, *, include_internal: bool = True, last_n: int | None = None
) -> list[Message]:
    stmt = select(Message).where(Message.business_id == ctx.business_id, Message.conversation_id == conversation_id)
    if not include_internal:
        stmt = stmt.where(Message.direction != "internal")
    if last_n:
        rows = list(db.scalars(stmt.order_by(Message.created_at.desc()).limit(last_n)))
        return rows[::-1]
    return list(db.scalars(stmt.order_by(Message.created_at)))


def get_thread(db: Session, ctx: Context, conversation_id: uuid.UUID, last_n: int = 20) -> ThreadDto:
    ctx.require(P.THREAD_READ)
    conv = _conv(db, ctx, conversation_id)
    return ThreadDto(
        conversation_id=conv.id,
        channel=conv.channel,
        status=conv.status,
        ai_mode=conv.ai_mode,
        ai_generation=conv.ai_generation,
        customer_id=conv.customer_id,
        visitor_name=conv.visitor_name,
        messages=[MessageOut.model_validate(m) for m in messages(db, ctx, conv.id, last_n=last_n)],
    )


# ------------------------------------------------------------------------------------------- visitor tokens
def visitor_message(db: Session, ctx: Context, conv: Conversation, body: str) -> Message:
    locked = lock_for_customer(db, ctx, conv.id)
    if locked.erased_at is not None or locked.restricted_at is not None:
        raise Conflict("conversations.erased_or_restricted")
    if locked.customer_id is not None:
        crm.get_customer_summary(db, ctx, locked.customer_id)
    return _add(
        db, ctx, locked, direction="inbound", author_kind="customer" if locked.customer_id else "visitor", body=body
    )
