"""M04 inbox rules: visitor isolation, message events, and outbound replies through the outbox."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.modules.notifications import public as notifications
from app.platform import audit, clock, crud, events
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid, NotFound
from app.platform.events import DomainEventPayload
from app.platform.ids import new_id

from . import permissions as P
from .consent import CONTACT_CONSENT_TEXT, promote_contact_consent
from .delivery import on_delivery_status as on_delivery_status
from .models import Conversation, Message
from .repository import counts as counts
from .repository import get_conversation as _conv
from .repository import lock_for_customer as lock_for_customer
from .repository import previews as previews
from .repository import public_messages as public_messages
from .schemas import ConversationCreate, ConversationUpdate, MessageContext, MessageOut, ThreadDto
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


class HandoffRequested(DomainEventPayload):
    event_type = "conversations.handoff_requested"
    conversation_id: uuid.UUID
    reason: str


def _timeline(db: Session, ctx: Context, conv: Conversation, msg: Message) -> None:
    if conv.customer_id is None or msg.direction == "internal":
        return
    word = "Message from customer" if msg.direction == "inbound" else "Reply sent"
    crm.add_timeline_entry(
        db,
        ctx,
        crm.TimelineEntryIn(
            customer_id=conv.customer_id,
            occurred_at=msg.created_at or clock.now(),
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
    )
    db.add(msg)
    conv.last_message_at = now
    if direction == "inbound":
        conv.status = "waiting_on_staff"
    elif direction == "outbound":
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


def send_reply(
    db: Session, ctx: Context, conversation_id: uuid.UUID, body: str, *, proposal_id: uuid.UUID | None = None
) -> Message:
    """Outbound message + outbox delivery in the caller's transaction (web chat: visible to the visitor)."""
    ctx.require(P.THREAD_WRITE)
    conv = lock_for_customer(db, ctx, conversation_id)
    if conv.restricted_at is not None or conv.erased_at is not None:
        raise Conflict("conversations.restricted", detail="Replies to a restricted conversation are blocked")
    if conv.customer_id is not None:
        crm.get_customer_summary(db, ctx, conv.customer_id)
    msg = _add(
        db, ctx, conv, direction="outbound", author_kind="staff", body=body, proposal_id=proposal_id or ctx.proposal_id
    )
    channel = "web_chat" if conv.channel == "web_chat" else "simulated"
    recipient = None
    if channel != "web_chat":
        recipient = (
            crm.get_customer_contact(db, ctx, conv.customer_id, "email") if conv.customer_id else None
        ) or conv.visitor_email
    msg.outbox_message_id = notifications.enqueue_message(
        db,
        ctx,
        channel=channel,
        recipient=recipient,
        template_key="conversation_reply",
        variables={"body": body},
        idempotency_key=f"reply:{msg.id}",
        related_type="message",
        related_id=msg.id,
        proposal_id=msg.proposal_id,
    )
    msg.delivery_status = "pending"
    audit.record(
        db,
        ctx,
        action="conversations.reply_sent",
        target_type="conversation",
        target_id=conv.id,
        summary=f"Reply queued ({channel.replace('_', ' ')})",
        proposal_id=msg.proposal_id,
    )
    return msg


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


def request_handoff(db: Session, ctx: Context, conversation_id: uuid.UUID, reason: str) -> None:
    conv = lock_for_customer(db, ctx, conversation_id)
    if conv.handoff_requested_at is not None:
        return
    conv.handoff_requested_at = clock.now()
    conv.status = "waiting_on_staff"
    events.publish(db, ctx, HandoffRequested(conversation_id=conv.id, reason=reason[:300]))
    staff = [conv.assigned_to] if conv.assigned_to else []
    notifications.notify_users(
        db,
        ctx,
        staff,
        kind="handoff",
        title="A conversation needs a person",
        body=reason[:300],
        link=f"/inbox/{conv.id}",
    )


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


def get_message_context(db: Session, ctx: Context, message_id: uuid.UUID) -> MessageContext:
    ctx.require(P.THREAD_READ)
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
    )


def get_thread(db: Session, ctx: Context, conversation_id: uuid.UUID, last_n: int = 20) -> ThreadDto:
    ctx.require(P.THREAD_READ)
    conv = _conv(db, ctx, conversation_id)
    return ThreadDto(
        conversation_id=conv.id,
        channel=conv.channel,
        status=conv.status,
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
