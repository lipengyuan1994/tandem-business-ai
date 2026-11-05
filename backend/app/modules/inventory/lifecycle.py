"""Inventory: export, and redaction of free-text notes about an erased person (M18)."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import (
    ReorderDraft,
    ReorderDraftLine,
    ReorderRule,
    StockLevel,
    StockLocation,
    StockMovement,
    StockReservation,
)


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(
        db,
        ctx,
        [StockLocation, StockLevel, StockMovement, StockReservation, ReorderRule, ReorderDraft, ReorderDraftLine],
    )
    return export_tables(tables, streaming=streaming)


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Quantities, costs and ids stay. Notes on movements for the person's records (a reservation for their
    appointment) are cleared; other notes and reorder drafts lose the person's names and addresses."""
    movements = int(
        db.scalar(select(func.erase_redact_stock_notes(ctx.business_id, target.needles, target.all_ids))) or 0
    )
    needles = target.needles
    drafts = db.execute(
        update(ReorderDraft)
        .where(
            ReorderDraft.business_id == ctx.business_id,
            ReorderDraft.note.is_distinct_from(func.redact_text(ReorderDraft.note, needles)),
        )
        .values(note=func.redact_text(ReorderDraft.note, needles))
    )
    lines = db.execute(
        update(ReorderDraftLine)
        .where(
            ReorderDraftLine.business_id == ctx.business_id,
            ReorderDraftLine.reason_text.is_distinct_from(func.redact_text(ReorderDraftLine.reason_text, needles)),
        )
        .values(reason_text=func.redact_text(ReorderDraftLine.reason_text, needles))
    )
    return {
        "stock_notes_redacted": movements,
        "reorder_drafts_redacted": rows_affected(drafts),
        "reorder_lines_redacted": rows_affected(lines),
    }
