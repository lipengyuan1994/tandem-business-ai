"""conversations.send_reply executor: an approved reply is posted and delivered through the outbox."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.org import public as org
from app.platform import crud
from app.platform.context import Context

from . import permissions as P
from . import service
from .models import Conversation
from .ownership import ensure_ai_active
from .repository import reply_blockers
from .schemas import REPLY_GUARDRAIL_VERSION, SendReplyPayload


def _describe(db: Session, ctx: Context, p: SendReplyPayload) -> list[PreviewLine]:
    conv = crud.get_scoped(db, ctx, Conversation, p.conversation_id)
    where = conv.channel.replace("_", " ") if conv else "conversation"
    return [PreviewLine(f"Reply ({where})", None, p.body)]


def _validate(db: Session, ctx: Context, p: SendReplyPayload) -> None:
    crud.require_scoped(db, ctx, Conversation, p.conversation_id, "conversations.not_found")
    if ctx.actor_kind == "assistant" or p.ai_generation is not None:
        p.ai_generation = ensure_ai_active(
            db,
            ctx,
            p.conversation_id,
            p.ai_generation,
            lock=ctx.actor_kind == "assistant",
            staff_requested=ctx.actor_kind != "assistant" or ctx.on_behalf_of_user_id is not None,
        )


def _run(db: Session, ctx: Context, p: SendReplyPayload, meta: ExecutionMeta) -> dict[str, Any]:
    authority = approvals.outbound_authority(db, ctx, meta.proposal_id)
    ai_reply = bool(authority and authority["origin_kind"] == "assistant")
    if ai_reply:
        ensure_ai_active(
            db,
            ctx,
            p.conversation_id,
            p.ai_generation,
            lock=True,
            staff_requested=bool(authority and authority.get("origin_user_id")),
        )
    msg = service.send_reply(
        db,
        ctx,
        p.conversation_id,
        p.body,
        proposal_id=meta.proposal_id,
        ai_reply=ai_reply,
        answers_message_id=p.answers_message_id,
        ai_generation=p.ai_generation,
    )
    return {"message_id": str(msg.id)}


def autonomy_reasons(db: Session, ctx: Context, p: SendReplyPayload) -> list[str]:
    reasons = []
    if p.answers_message_id is None or (ctx.actor_kind == "assistant" and ctx.on_behalf_of_user_id is not None):
        reasons.append("not_intake")
    if not p.safety_clean:
        reasons.extend(["safety_unchecked", "critical_topic"])
    if p.care_category != "none":
        reasons.append("critical_topic")
    current_hash = org.reply_guardrail_hash(org.get_settings(db, ctx).ai_reply_guardrail)
    if p.guardrail_hash != current_hash:
        reasons.append("guardrail_changed" if p.guardrail_hash else "guardrail_unavailable")
    if current_hash:
        if p.guardrail is None or p.guardrail_version != REPLY_GUARDRAIL_VERSION:
            reasons.append("guardrail_unavailable")
        elif p.guardrail.verdict == "needs_staff":
            reasons.append("guardrail_needs_staff")
    if p.grounding is None or p.grounding.verdict == "unavailable":
        reasons.append("grounding_unavailable")
    elif p.grounding.verdict != "supported":
        reasons.append("contradicted" if p.grounding.verdict == "contradicted" else "ungrounded")
    if p.bundled_action:
        reasons.append("bundled_action")
    reasons.extend(reply_blockers(db, ctx, p.conversation_id, p.ai_generation, p.answers_message_id))
    return list(dict.fromkeys(reasons))


SEND_REPLY = ExecutorSpec(
    "conversations.send_reply",
    SendReplyPayload,
    P.PROPOSAL_APPROVE,
    _describe,
    _validate,
    _run,
    autonomy_reasons=autonomy_reasons,
)
