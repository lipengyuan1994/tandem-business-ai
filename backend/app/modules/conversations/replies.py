"""Immediate staff/email replies and executor-owned withheld web-chat replies."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.crm import public as crm
from app.modules.notifications import public as notifications
from app.platform import audit
from app.platform.context import Context
from app.platform.errors import Conflict

from . import permissions as P
from . import publication
from .models import Message
from .ownership import pause
from .repository import get_message_context, lock_for_customer


def send_reply(
    db: Session,
    ctx: Context,
    conversation_id: uuid.UUID,
    body: str,
    *,
    proposal_id: uuid.UUID | None = None,
    ai_reply: bool = False,
    answers_message_id: uuid.UUID | None = None,
    ai_generation: int | None = None,
    not_before: datetime | None = None,
) -> Message:
    """Queue AI web replies for publication; manual replies and email retain their existing delivery path."""
    from .service import _add

    ctx.require(P.THREAD_WRITE)
    proposal_id = proposal_id or ctx.proposal_id
    authority = approvals.outbound_authority(db, ctx, proposal_id) if proposal_id else None
    if authority and authority["origin_kind"] == "assistant":
        ai_reply = True
        if answers_message_id is None and authority.get("source_message_id"):
            source_id = uuid.UUID(authority["source_message_id"])
            source = get_message_context(db, ctx, source_id)
            if source.conversation_id == conversation_id:
                answers_message_id, ai_generation = source_id, source.message_ai_generation
    conv = lock_for_customer(db, ctx, conversation_id)
    if conv.restricted_at is not None or conv.erased_at is not None:
        raise Conflict("conversations.restricted", detail="Replies to a restricted conversation are blocked")
    if conv.customer_id is not None:
        crm.get_customer_summary(db, ctx, conv.customer_id)
    if not ai_reply:
        pause(db, ctx, conv)
    msg = _add(
        db,
        ctx,
        conv,
        direction="outbound",
        author_kind="assistant" if ai_reply else "staff",
        body=body,
        proposal_id=proposal_id or ctx.proposal_id,
        withheld=ai_reply and conv.channel == "web_chat",
        answers_message_id=answers_message_id,
        ai_generation=ai_generation,
    )
    if msg.publication_state == "withheld":
        if not_before is not None:
            msg.publish_at = max(msg.publish_at or not_before, not_before)
        msg.delivery_status = "withheld"
        publication.schedule(db, ctx, msg)
        audit.record(
            db,
            ctx,
            action="conversations.reply_withheld",
            target_type="message",
            target_id=msg.id,
            summary="Web-chat reply awaiting send delay",
            proposal_id=msg.proposal_id,
        )
        return msg
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
