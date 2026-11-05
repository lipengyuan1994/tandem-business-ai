"""/api/v1/inventory."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require

from . import permissions as P
from . import public, service
from .models import ReorderDraft, StockMovement, StockReservation
from .schemas import (
    AvailabilityOut,
    CountIn,
    DraftLineIn,
    ItemStockDetail,
    MovementIn,
    MovementOut,
    ReorderDraftIn,
    ReorderDraftOut,
    ReorderRuleIn,
    ReorderRuleOut,
    ReorderSuggestion,
    ReservationOut,
)

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


@router.get(
    "/levels",
    response_model=list[AvailabilityOut],
    summary="On hand, reserved and available per product",
    responses=ERRORS,
)
def levels(
    below_reorder: bool = False, q: str | None = None, ctx: Context = Depends(require(P.STOCK_READ))
) -> list[AvailabilityOut]:
    with transaction(ctx) as db:
        rows = service.availability(db, ctx)
        if below_reorder:
            rows = [r for r in rows if r.below_reorder]
        if q:
            rows = [r for r in rows if q.lower() in r.item_name.lower() or (r.sku or "").lower() == q.lower()]
        return rows


@router.get(
    "/items/{item_id}",
    response_model=ItemStockDetail,
    summary="Stock detail: levels, ledger, reservations, rule",
    responses=ERRORS,
)
def item_detail(item_id: uuid.UUID, ctx: Context = Depends(require(P.STOCK_READ))) -> ItemStockDetail:
    with transaction(ctx) as db:
        lv = service.availability(db, ctx, [item_id])
        movements = db.scalars(
            select(StockMovement)
            .where(StockMovement.business_id == ctx.business_id, StockMovement.item_id == item_id)
            .order_by(StockMovement.occurred_at.desc())
            .limit(100)
        )
        reservations = db.scalars(
            select(StockReservation)
            .where(
                StockReservation.business_id == ctx.business_id,
                StockReservation.item_id == item_id,
                StockReservation.status == "active",
            )
            .order_by(StockReservation.created_at.desc())
        )
        rule = service._rule(db, ctx, item_id, service.default_location(db, ctx).id)
        return ItemStockDetail(
            levels=lv,
            movements=[MovementOut.model_validate(m) for m in movements],
            reservations=[ReservationOut.model_validate(r) for r in reservations],
            rule=ReorderRuleOut.model_validate(rule) if rule else None,
        )


@router.post(
    "/movements",
    response_model=MovementOut,
    status_code=201,
    summary="Record a receipt or adjustment (reason required)",
    responses=ERRORS,
)
def add_movement(body: MovementIn, ctx: Context = Depends(require(P.STOCK_ADJUST))) -> MovementOut:
    with transaction(ctx) as db:
        return MovementOut.model_validate(service.adjust(db, ctx, body))


@router.post(
    "/counts",
    response_model=MovementOut | None,
    summary="Record a stock count (writes the difference)",
    responses=ERRORS,
)
def add_count(body: CountIn, ctx: Context = Depends(require(P.STOCK_ADJUST))) -> MovementOut | None:
    with transaction(ctx) as db:
        row = service.count(db, ctx, body)
        return MovementOut.model_validate(row) if row else None


@router.put(
    "/reorder-rules/{item_id}",
    response_model=ReorderRuleOut,
    summary="Set the reorder rule for a product",
    responses=ERRORS,
)
def put_rule(
    item_id: uuid.UUID, body: ReorderRuleIn, ctx: Context = Depends(require(P.REORDER_WRITE))
) -> ReorderRuleOut:
    with transaction(ctx) as db:
        return ReorderRuleOut.model_validate(service.put_rule(db, ctx, item_id, body))


@router.get(
    "/reorder-suggestions",
    response_model=list[ReorderSuggestion],
    summary="Deterministic reorder suggestions",
    responses=ERRORS,
)
def reorder_suggestions(ctx: Context = Depends(require(P.STOCK_READ))) -> list[ReorderSuggestion]:
    with transaction(ctx) as db:
        return service.suggestions(db, ctx)


@router.get("/reorder-drafts", response_model=list[ReorderDraftOut], summary="Reorder drafts", responses=ERRORS)
def list_drafts(ctx: Context = Depends(require(P.REORDER_READ))) -> list[ReorderDraftOut]:
    with transaction(ctx) as db:
        drafts = list(
            db.scalars(
                select(ReorderDraft)
                .where(ReorderDraft.business_id == ctx.business_id)
                .order_by(ReorderDraft.created_at.desc())
                .limit(50)
            )
        )
        lines = service.draft_lines(db, ctx, [d.id for d in drafts])
        return [
            ReorderDraftOut(
                id=d.id,
                status=d.status,
                note=d.note,
                created_at=d.created_at,
                lines=[
                    DraftLineIn(item_id=ln.item_id, quantity=ln.quantity, reason_text=ln.reason_text)
                    for ln in lines.get(d.id, [])
                ],
            )
            for d in drafts
        ]


@router.post(
    "/reorder-drafts",
    response_model=ReorderDraftOut,
    status_code=201,
    summary="Create a reorder draft",
    responses=ERRORS,
)
def create_draft(body: ReorderDraftIn, ctx: Context = Depends(require(P.REORDER_WRITE))) -> ReorderDraftOut:
    with transaction(ctx) as db:
        d = service.create_draft(db, ctx, body.lines, body.note)
        return ReorderDraftOut(id=d.id, status=d.status, note=d.note, created_at=d.created_at, lines=body.lines)


@router.get(
    "/reorder-drafts/{draft_id}",
    response_model=ReorderDraftOut,
    summary="Read a reorder draft linked from Activity",
    responses=ERRORS,
)
def get_draft(draft_id: uuid.UUID, ctx: Context = Depends(require(P.REORDER_READ))) -> ReorderDraftOut:
    with transaction(ctx) as db:
        draft = public.read_reorder_draft(db, ctx, draft_id)
        return ReorderDraftOut(**draft.model_dump(exclude={"version"}))
