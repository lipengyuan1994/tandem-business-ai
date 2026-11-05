"""Owned-thread provenance and explicit action cards; models cannot call this approval path."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import NotFound

from ..models import AiRun, AssistantMessage, AssistantThread


def owned(db: Session, ctx: Context, thread_id: uuid.UUID, *, lock: bool = False) -> AssistantThread:
    ctx.require("ai.assistant.use")
    stmt = select(AssistantThread).where(
        AssistantThread.business_id == ctx.business_id,
        AssistantThread.user_id == ctx.user_id,
        AssistantThread.id == thread_id,
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound("ai.thread_not_found")
    return row


def linked_run(db: Session, ctx: Context, thread_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(AiRun.id)
            .join(
                AssistantMessage,
                (AssistantMessage.run_id == AiRun.id) & (AssistantMessage.business_id == AiRun.business_id),
            )
            .where(
                AiRun.business_id == ctx.business_id,
                AiRun.id == run_id,
                AiRun.use_case == "copilot",
                AiRun.on_behalf_of_user_id == ctx.user_id,
                AssistantMessage.thread_id == thread_id,
                AssistantMessage.role == "assistant",
            )
            .limit(1)
        )
        is not None
    )


def cards(db: Session, ctx: Context, message: AssistantMessage) -> list[approvals.ChatCard]:
    if message.run_id is None or not linked_run(db, ctx, message.thread_id, message.run_id):
        return []
    out = []
    for proposal_id in message.proposal_ids:
        try:
            out.append(approvals.chat_card(db, ctx, proposal_id, run_id=message.run_id))
        except NotFound:
            continue  # expired retention/erasure links do not expose another user's proposal
    return out


def confirm(
    ctx: Context, thread_id: uuid.UUID, proposal_id: uuid.UUID, body: approvals.ChatConfirmIn
) -> approvals.ChatCard:
    with transaction(ctx) as db:
        owned(db, ctx, thread_id)
        run_id = approvals.chat_origin_run_id(db, ctx, proposal_id)
        if not linked_run(db, ctx, thread_id, run_id):
            raise NotFound("ai.proposal_not_in_thread")
        approvals.confirm_chat(
            db,
            ctx,
            proposal_id,
            run_id=run_id,
            revision_id=body.revision_id,
            payload_hash=body.payload_hash,
        )
        return approvals.chat_card(db, ctx, proposal_id, run_id=run_id)
