"""conversations.send_reply executor: an approved reply is posted and delivered through the outbox."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.platform import crud
from app.platform.context import Context

from . import permissions as P
from . import service
from .models import Conversation
from .schemas import SendReplyPayload


def _describe(db: Session, ctx: Context, p: SendReplyPayload) -> list[PreviewLine]:
    conv = crud.get_scoped(db, ctx, Conversation, p.conversation_id)
    where = conv.channel.replace("_", " ") if conv else "conversation"
    return [PreviewLine(f"Reply ({where})", None, p.body)]


def _validate(db: Session, ctx: Context, p: SendReplyPayload) -> None:
    crud.require_scoped(db, ctx, Conversation, p.conversation_id, "conversations.not_found")


def _run(db: Session, ctx: Context, p: SendReplyPayload, meta: ExecutionMeta) -> dict[str, Any]:
    msg = service.send_reply(db, ctx, p.conversation_id, p.body, proposal_id=meta.proposal_id)
    return {"message_id": str(msg.id)}


SEND_REPLY = ExecutorSpec("conversations.send_reply", SendReplyPayload, P.PROPOSAL_APPROVE, _describe, _validate, _run)
