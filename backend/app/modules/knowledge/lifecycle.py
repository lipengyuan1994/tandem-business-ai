"""Business export, erasure and bounded retention of visitor-derived suggestion text."""

from datetime import timedelta
from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from app.modules.conversations import public as conversations
from app.modules.org import public as org
from app.platform import clock, jobs, retention
from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import Article, ArticleVersion, Suggestion


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    return export_tables(stream_models(db, ctx, [Article, ArticleVersion, Suggestion]), streaming=streaming)


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    value = case(
        (Suggestion.conversation_id.in_(target.all_ids), "[erased]"),
        else_=func.redact_text(Suggestion.question_text, target.needles),
    )
    rows = db.execute(
        update(Suggestion)
        .where(Suggestion.business_id == ctx.business_id, Suggestion.question_text.is_distinct_from(value))
        .values(
            question_text=value,
            status=case((Suggestion.status == "open", "dismissed"), else_=Suggestion.status),
            version=Suggestion.version + 1,
        )
    )
    return {"kb_suggestions_redacted": rows_affected(rows)}


def cleanup(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    """Only resolved suggestions expire, measured from their last update, using conversation retention."""
    if ctx.business_id is None:
        raise ValueError("knowledge.retention requires a business")
    cutoff = clock.now() - timedelta(days=org.get_settings(db, ctx).retention_days_conversations)
    candidates = (
        select(Suggestion.id)
        .where(
            Suggestion.business_id == ctx.business_id,
            Suggestion.status.in_(("dismissed", "done")),
            Suggestion.updated_at < cutoff,
            Suggestion.conversation_id.in_(conversations.retention_eligible_conversations(ctx)),
        )
        .order_by(Suggestion.updated_at, Suggestion.id)
        .limit(retention.BATCH)
        .with_for_update(skip_locked=True)
    )
    count = rows_affected(
        db.execute(
            delete(Suggestion)
            .where(Suggestion.business_id == ctx.business_id, Suggestion.id.in_(candidates))
            .execution_options(synchronize_session=False)
        )
    )
    if count == retention.BATCH:
        run_at = clock.now() + timedelta(minutes=1)
        jobs.enqueue(
            db,
            "knowledge.retention",
            business_id=ctx.business_id,
            run_at=run_at,
            priority=200,
            dedupe_key=f"retention:knowledge:{ctx.business_id}:{int(run_at.timestamp()) // 60}",
        )
