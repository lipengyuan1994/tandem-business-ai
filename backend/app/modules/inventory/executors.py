"""inventory.adjust_stock and inventory.create_reorder_draft executors."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.catalog import public as catalog
from app.platform.context import Context
from app.platform.quantities import format_quantity

from . import permissions as P
from . import service
from .schemas import AdjustStockPayload, MovementIn, ReorderDraftIn


def _describe_adjust(db: Session, ctx: Context, p: AdjustStockPayload) -> list[PreviewLine]:
    a = next(iter(service.availability(db, ctx, [p.item_id])), None)
    name = a.item_name if a else str(p.item_id)
    before = a.on_hand if a else None
    return [
        PreviewLine(
            f"{name} on hand",
            format_quantity(before) if before is not None else None,
            f"{format_quantity((before or 0) + p.quantity)} ({p.reason.replace('_', ' ')})",
        )
    ]


def _validate_adjust(db: Session, ctx: Context, p: AdjustStockPayload) -> None:
    service.availability(db, ctx, [p.item_id])


def _run_adjust(db: Session, ctx: Context, p: AdjustStockPayload, meta: ExecutionMeta) -> dict[str, Any]:
    row = service.adjust(
        db,
        ctx,
        MovementIn(
            item_id=p.item_id, quantity=p.quantity, reason=p.reason, note=p.note, stock_location_id=p.stock_location_id
        ),
    )
    return {"movement_id": str(row.id)}


def _describe_draft(db: Session, ctx: Context, p: ReorderDraftIn) -> list[PreviewLine]:
    names = catalog.get_items(db, ctx, {ln.item_id for ln in p.lines})
    return [
        PreviewLine(
            f"Order {names[ln.item_id].name if ln.item_id in names else ln.item_id}", None, format_quantity(ln.quantity)
        )
        for ln in p.lines
    ]


def _validate_draft(db: Session, ctx: Context, p: ReorderDraftIn) -> None:
    return None


def _run_draft(db: Session, ctx: Context, p: ReorderDraftIn, meta: ExecutionMeta) -> dict[str, Any]:
    return {"reorder_draft_id": str(service.create_draft(db, ctx, p.lines, p.note).id)}


ADJUST = ExecutorSpec(
    "inventory.adjust_stock", AdjustStockPayload, P.PROPOSAL_APPROVE, _describe_adjust, _validate_adjust, _run_adjust
)
DRAFT = ExecutorSpec(
    "inventory.create_reorder_draft", ReorderDraftIn, P.PROPOSAL_APPROVE, _describe_draft, _validate_draft, _run_draft
)
