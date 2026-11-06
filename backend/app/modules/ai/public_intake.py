"""Per-business public intake admission. The message is already durable before event delivery.

A transaction advisory lock serializes concurrent event deliveries and job admission. Staff AI never
uses this quota. The pending count includes queued retries and running jobs.
"""

from __future__ import annotations

import uuid
from datetime import UTC

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.conversations import public as conversations
from app.platform import clock
from app.platform.config import get_settings
from app.platform.context import Context, system_context
from app.platform.errors import NotFound
from app.platform.models import Job

from .budget_models import PublicAllowance

HOURLY_LIMIT_NOTE = "Hourly public AI intake limit reached."
QUEUE_LIMIT_NOTE = "AI intake queue is full."


def _handoff_body(message_id: uuid.UUID, reason: str) -> str:
    return f"{reason} No AI call was made; please handle this message. [intake:{message_id}]"


def handoff(db: Session, ctx: Context, message_id: uuid.UUID, reason: str) -> None:
    ctx = system_context(ctx.business_id, frozenset({"conversations.thread.read"}))
    message = conversations.get_message_context(db, ctx, message_id)
    marker = f"[intake:{message_id}]"
    conversations.request_handoff(db, ctx, message.conversation_id, reason=reason)
    if not conversations.has_internal_note_marker(db, ctx, message.conversation_id, marker):
        conversations.add_internal_note(
            db,
            ctx,
            message.conversation_id,
            _handoff_body(message_id, reason),
            author_kind="assistant",
        )


def admit(db: Session, ctx: Context, message_id: uuid.UUID) -> bool:
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"ai-public-intake:{ctx.business_id}"}
    )
    if db.scalar(select(Job.id).where(Job.dedupe_key == f"intake:{message_id}")):
        return False  # at-least-once event: do not spend admission twice
    read_ctx = system_context(ctx.business_id, frozenset({"conversations.thread.read"}))
    try:
        message = conversations.get_message_context(db, read_ctx, message_id)
    except NotFound:
        # A stale event may precede message availability/deletion; retain the existing worker behavior.
        pass
    else:
        if conversations.has_internal_note_marker(db, read_ctx, message.conversation_id, f"[intake:{message_id}]"):
            return False  # A completed/handoff decision stays terminal even after quotas reset or jobs expire.
    settings = get_settings()
    pending = (
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.business_id == ctx.business_id,
                Job.kind == "ai.intake",
                Job.status.in_(("queued", "running")),
            )
        )
        or 0
    )
    if pending >= settings.ai_public_intake_max_pending:
        handoff(db, ctx, message_id, QUEUE_LIMIT_NOTE)
        return False
    period = clock.now().astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    db.execute(
        insert(PublicAllowance)
        .values(
            business_id=ctx.business_id,
            kind="hour",
            period=period,
            used=0,
        )
        .on_conflict_do_nothing()
    )
    claimed = db.scalar(
        update(PublicAllowance)
        .where(
            PublicAllowance.business_id == ctx.business_id,
            PublicAllowance.kind == "hour",
            PublicAllowance.period == period,
            PublicAllowance.used < settings.ai_public_intake_per_hour,
        )
        .values(used=PublicAllowance.used + 1)
        .returning(PublicAllowance.id)
    )
    if claimed is None:
        handoff(db, ctx, message_id, HOURLY_LIMIT_NOTE)
        return False
    return True


def admit_run(db: Session, ctx: Context, message_id: uuid.UUID) -> bool:
    """Also gate execution (old queued work and direct/retried jobs), before the safety model."""
    read_ctx = system_context(ctx.business_id, frozenset({"conversations.thread.read"}))
    message = conversations.get_message_context(db, read_ctx, message_id)
    if any(
        conversations.has_internal_note_marker(db, read_ctx, message.conversation_id, _handoff_body(message_id, reason))
        for reason in (HOURLY_LIMIT_NOTE, QUEUE_LIMIT_NOTE)
    ):
        return False
    return admit_classification(db, ctx, message_id)


def admit_classification(db: Session, ctx: Context, message_id: uuid.UUID) -> bool:
    """Shared hourly run ceiling, independent of Front Desk reply admission/ownership."""
    period = clock.now().astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    db.execute(
        insert(PublicAllowance)
        .values(
            business_id=ctx.business_id,
            kind="runs",
            period=period,
            used=0,
        )
        .on_conflict_do_nothing()
    )
    claimed = db.scalar(
        update(PublicAllowance)
        .where(
            PublicAllowance.business_id == ctx.business_id,
            PublicAllowance.kind == "runs",
            PublicAllowance.period == period,
            PublicAllowance.used < get_settings().ai_public_intake_per_hour,
        )
        .values(used=PublicAllowance.used + 1)
        .returning(PublicAllowance.id)
    )
    if claimed is not None:
        return True
    handoff(db, ctx, message_id, HOURLY_LIMIT_NOTE)
    return False
