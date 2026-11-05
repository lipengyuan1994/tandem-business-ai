"""inventory.adjust_stock and inventory.create_reorder_draft executors."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.catalog import public as catalog
from app.platform.context import Context
from app.platform.errors import Conflict
from app.platform.quantities import format_quantity

from . import permissions as P
from . import service
from .models import ReorderRule
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
    below = {line.item_id: line for line in service.suggestions(db, ctx)}
    preview = []
    for line in p.lines:
        name = names[line.item_id].name if line.item_id in names else str(line.item_id)
        preview.append(PreviewLine(f"Order {name}", None, format_quantity(line.quantity)))
        source = line.reason_text
        if not source or not source.startswith("Quantity requested by "):
            suggested = below[line.item_id].suggested if line.item_id in below else None
            label = (
                f"Suggested {format_quantity(line.quantity)}"
                if line.quantity == suggested
                else f"Quantity {format_quantity(line.quantity)} (suggested "
                f"{format_quantity(suggested) if suggested is not None else 'none'})"
            )
            source = label + (f" · {line.reason_text}" if line.reason_text else "")
        preview.append(PreviewLine(f"Quantity source · {name}", None, source))
    return preview


def _autonomy_reasons(db: Session, ctx: Context, p: ReorderDraftIn) -> list[str]:
    below = {line.item_id: line for line in service.suggestions(db, ctx)}
    return (
        ["quantity_not_suggested"]
        if any(line.item_id not in below or line.quantity != below[line.item_id].suggested for line in p.lines)
        else []
    )


def _validate_draft(db: Session, ctx: Context, p: ReorderDraftIn) -> None:
    for item_id in sorted({line.item_id for line in p.lines}):
        if service.has_open_reorder_draft(db, ctx, item_id, lock=True):
            raise Conflict("inventory.open_reorder_draft")
    if ctx.actor_kind == "assistant" or ctx.autonomy_rule_id is not None:
        if len({line.item_id for line in p.lines}) != len(p.lines):
            raise Conflict("inventory.reorder_changed")
    if ctx.autonomy_rule_id is not None:
        location = service.default_location(db, ctx)
        for item_id in sorted({line.item_id for line in p.lines}):
            service._locked_level(db, ctx, item_id, location.id)
        list(
            db.scalars(
                select(ReorderRule)
                .where(
                    ReorderRule.business_id == ctx.business_id,
                    ReorderRule.item_id.in_({line.item_id for line in p.lines}),
                    ReorderRule.stock_location_id == location.id,
                )
                .order_by(ReorderRule.item_id)
                .with_for_update(read=True)
            )
        )
        below = {s.item_id: s for s in service.suggestions(db, ctx)}
        if any(line.item_id not in below or line.quantity != below[line.item_id].suggested for line in p.lines):
            raise Conflict("inventory.reorder_changed")


def _run_draft(db: Session, ctx: Context, p: ReorderDraftIn, meta: ExecutionMeta) -> dict[str, Any]:
    _validate_draft(db, ctx, p)
    return {"reorder_draft_id": str(service.create_draft(db, ctx, p.lines, p.note).id)}


ADJUST = ExecutorSpec(
    "inventory.adjust_stock", AdjustStockPayload, P.PROPOSAL_APPROVE, _describe_adjust, _validate_adjust, _run_adjust
)
DRAFT = ExecutorSpec(
    "inventory.create_reorder_draft",
    ReorderDraftIn,
    P.PROPOSAL_APPROVE,
    _describe_draft,
    _validate_draft,
    _run_draft,
    autonomy_reasons=_autonomy_reasons,
)
