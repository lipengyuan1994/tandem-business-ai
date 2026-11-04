"""Public API of the inventory module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.platform.context import Context

from . import service
from .schemas import AvailabilityOut, MovementIn, ReorderDraftRead, ReorderSuggestion
from .service import StockAdjusted, StockBelowReorderLevel


def get_availability(db: Session, ctx: Context, item_ids: list[uuid.UUID]) -> list[AvailabilityOut]:
    return service.availability(db, ctx, item_ids)


def reserve_stock(
    db: Session, ctx: Context, *, item_id: uuid.UUID, quantity: Decimal, ref_type: str, ref_id: uuid.UUID
) -> uuid.UUID:
    return service.reserve(db, ctx, item_id=item_id, quantity=quantity, ref_type=ref_type, ref_id=ref_id).id


def release_reservations(db: Session, ctx: Context, *, ref_type: str, ref_id: uuid.UUID) -> int:
    return service.release(db, ctx, ref_type=ref_type, ref_id=ref_id)


def consume_reservations(
    db: Session,
    ctx: Context,
    *,
    ref_type: str,
    ref_id: uuid.UUID,
    reason: Literal["consumption", "sale"] = "consumption",
) -> int:
    return service.consume(db, ctx, ref_type=ref_type, ref_id=ref_id, reason=reason)


def record_movement(db: Session, ctx: Context, m: MovementIn) -> uuid.UUID:
    return service.record_movement(db, ctx, m).id


def list_below_reorder(db: Session, ctx: Context) -> list[ReorderSuggestion]:
    return service.suggestions(db, ctx)


__all__ = [
    "AvailabilityOut",
    "MovementIn",
    "ReorderDraftRead",
    "ReorderSuggestion",
    "StockAdjusted",
    "StockBelowReorderLevel",
    "consume_reservations",
    "get_availability",
    "list_below_reorder",
    "mark_reorder_draft_converted",
    "read_reorder_draft",
    "record_movement",
    "release_reservations",
    "reserve_stock",
]


def read_reorder_draft(db: Session, ctx: Context, draft_id: uuid.UUID, *, lock: bool = False) -> ReorderDraftRead:
    """Internal purchasing read. Caller gates access; lock serializes conversion in the caller's transaction."""
    from app.platform.crud import require_scoped

    from .models import ReorderDraft
    from .schemas import DraftLineIn

    row = require_scoped(db, ctx, ReorderDraft, draft_id, "inventory.reorder_draft_not_found", for_update=lock)
    return ReorderDraftRead(
        id=row.id,
        status=row.status,
        note=row.note,
        version=row.version,
        created_at=row.created_at,
        lines=[
            DraftLineIn.model_validate(line, from_attributes=True)
            for line in service.draft_lines(db, ctx, [draft_id]).get(draft_id, [])
        ],
    )


def mark_reorder_draft_converted(db: Session, ctx: Context, draft_id: uuid.UUID, version: int) -> None:
    """Mark a locked, unchanged draft sent_to_purchasing; purchasing owns the resulting PO reference."""
    from app.platform import audit
    from app.platform.crud import require_scoped
    from app.platform.errors import Conflict

    from .models import ReorderDraft

    row = require_scoped(db, ctx, ReorderDraft, draft_id, "inventory.reorder_draft_not_found", for_update=True)
    if row.version != version:
        raise Conflict("inventory.version_conflict", current_version=row.version)
    if row.status != "draft":
        raise Conflict("inventory.draft_already_converted")
    row.status = "sent_to_purchasing"
    row.version += 1
    audit.record(
        db,
        ctx,
        action="inventory.reorder_draft.converted",
        target_type="reorder_draft",
        target_id=row.id,
        summary="Reorder draft converted to purchase order",
    )
    db.flush()
