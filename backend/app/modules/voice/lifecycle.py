"""Only operational metadata exists here; no audio or text is exported, erased or retained."""

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.platform import clock, retention
from app.platform.context import Context
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import VoiceSession
from .service import forget


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    return export_tables(stream_models(db, ctx, [VoiceSession]), streaming=streaming)


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    ids = list(
        db.scalars(
            delete(VoiceSession)
            .where(VoiceSession.business_id == ctx.business_id, VoiceSession.user_id.in_(target.all_ids))
            .returning(VoiceSession.id)
        )
    )
    forget(ids)
    return {"voice_sessions_deleted": len(ids)}


def cleanup(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    candidates = (
        select(VoiceSession.id)
        .where(VoiceSession.business_id == ctx.business_id, VoiceSession.created_at < clock.now() - timedelta(days=90))
        .order_by(VoiceSession.created_at, VoiceSession.id)
        .limit(retention.BATCH)
        .with_for_update(skip_locked=True)
    )
    ids = list(db.scalars(delete(VoiceSession).where(VoiceSession.id.in_(candidates)).returning(VoiceSession.id)))
    forget(ids)
    if len(ids) == retention.BATCH:
        from app.platform import jobs

        jobs.enqueue(
            db,
            "ai.assistant_retention",
            business_id=ctx.business_id,
            run_at=clock.now() + timedelta(minutes=1),
            dedupe_key=f"retention:voice:{ctx.business_id}:{int(clock.now().timestamp()) // 60}",
        )
