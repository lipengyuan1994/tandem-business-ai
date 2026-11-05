"""Owned copilot thread DTOs and reads, separate from streaming orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.modules.approvals import public as approvals
from app.platform import clock
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Unauthenticated

from ..models import AssistantMessage, AssistantThread
from .copilot_cards import cards
from .copilot_cards import owned as _owned
from .summary import Operation


class ThreadCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=120)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class CopilotThreadOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ThreadMessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    run_id: uuid.UUID | None
    proposal_ids: list[uuid.UUID]
    proposals: list[approvals.ChatCard] = Field(default_factory=list)
    created_at: datetime


class CopilotThreadDetailOut(CopilotThreadOut):
    messages: list[ThreadMessageOut]


def _staff(ctx: Context) -> tuple[uuid.UUID, uuid.UUID]:
    ctx.require("ai.assistant.use")
    if ctx.business_id is None or ctx.user_id is None:
        raise Unauthenticated("auth.unauthenticated")
    return ctx.business_id, ctx.user_id


def list_threads(ctx: Context) -> list[CopilotThreadOut]:
    business_id, user_id = _staff(ctx)
    with transaction(ctx) as db:
        rows = db.scalars(select(AssistantThread).where(
            AssistantThread.business_id == business_id, AssistantThread.user_id == user_id,
        ).order_by(AssistantThread.created_at.desc(), AssistantThread.id.desc()).limit(50))  # fmt: skip
        return [CopilotThreadOut.model_validate(row, from_attributes=True) for row in rows]


def create_thread(ctx: Context, body: ThreadCreate) -> CopilotThreadOut:
    business_id, user_id = _staff(ctx)
    with transaction(ctx) as db:
        thread = AssistantThread(business_id=business_id, user_id=user_id, title=body.title)
        db.add(thread)
        db.flush()
        return CopilotThreadOut.model_validate(thread, from_attributes=True)


def get_thread(ctx: Context, thread_id: uuid.UUID) -> CopilotThreadDetailOut:
    with transaction(ctx) as db:
        thread = _owned(db, ctx, thread_id)
        rows = db.scalars(select(AssistantMessage).where(
            AssistantMessage.business_id == ctx.business_id, AssistantMessage.thread_id == thread_id,
        ).order_by(AssistantMessage.created_at, AssistantMessage.id))  # fmt: skip
        return CopilotThreadDetailOut(
            **CopilotThreadOut.model_validate(thread, from_attributes=True).model_dump(),
            messages=[
                ThreadMessageOut.model_validate(row, from_attributes=True).model_copy(
                    update={"proposals": cards(db, ctx, row)}
                )
                for row in rows
            ],
        )


def retain_messages(ctx: Context) -> None:
    """Per-business retention: strictly older than 90 days, then threads with no remaining messages."""
    if ctx.business_id is None:
        raise ValueError("ai.assistant_retention requires a business")
    with transaction(ctx) as db:
        old_messages = select(AssistantMessage.id).where(
            AssistantMessage.business_id == ctx.business_id,
            AssistantMessage.created_at < clock.now() - timedelta(days=90),
        )
        db.execute(
            delete(Operation).where(Operation.business_id == ctx.business_id, Operation.message_id.in_(old_messages))
        )
        db.execute(delete(AssistantMessage).where(
            AssistantMessage.business_id == ctx.business_id,
            AssistantMessage.created_at < clock.now() - timedelta(days=90),
        ))  # fmt: skip
        has_message = select(AssistantMessage.id).where(
            AssistantMessage.business_id == ctx.business_id, AssistantMessage.thread_id == AssistantThread.id,
        ).exists()  # fmt: skip
        db.execute(delete(AssistantThread).where(
            AssistantThread.business_id == ctx.business_id, ~has_message,
        ))  # fmt: skip
