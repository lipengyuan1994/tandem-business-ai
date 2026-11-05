"""Stock ledger (M07).

Levels change only here, after locking the level row, in the same transaction as the ledger row.
"""

from __future__ import annotations

import math
import uuid
from decimal import Decimal
from typing import Literal

from sqlalchemy import and_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.org import public as org
from app.platform import audit, clock, events
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid, NotFound
from app.platform.events import DomainEventPayload
from app.platform.ids import new_id
from app.platform.quantities import format_quantity

from . import permissions as P
from .models import (
    ReorderDraft,
    ReorderDraftLine,
    ReorderRule,
    StockLevel,
    StockLocation,
    StockMovement,
    StockReservation,
)
from .schemas import AvailabilityOut, CountIn, DraftLineIn, MovementIn, ReorderRuleIn, ReorderSuggestion

ZERO = Decimal(0)


class StockBelowReorderLevel(DomainEventPayload):
    event_type = "inventory.stock_below_reorder_level"
    item_id: uuid.UUID
    stock_location_id: uuid.UUID
    available: Decimal
    reorder_point: Decimal


class StockAdjusted(DomainEventPayload):
    event_type = "inventory.stock_adjusted"
    item_id: uuid.UUID
    quantity: Decimal
    reason: str


def default_location(db: Session, ctx: Context) -> StockLocation:
    loc = db.scalar(
        select(StockLocation).where(StockLocation.business_id == ctx.business_id, StockLocation.is_default.is_(True))
    )
    if loc is None:
        assert ctx.business_id is not None
        db.execute(
            pg_insert(StockLocation)
            .values(id=new_id(), business_id=ctx.business_id, name="Main stock", is_default=True)
            .on_conflict_do_nothing()
        )
        loc = db.scalar(
            select(StockLocation).where(
                StockLocation.business_id == ctx.business_id, StockLocation.is_default.is_(True)
            )
        )
    assert loc is not None
    return loc


def _location_id(db: Session, ctx: Context, stock_location_id: uuid.UUID | None) -> uuid.UUID:
    if stock_location_id is None:
        return default_location(db, ctx).id
    found = db.scalar(
        select(StockLocation.id).where(
            StockLocation.business_id == ctx.business_id, StockLocation.id == stock_location_id
        )
    )
    if found is None:
        raise NotFound("inventory.stock_location_not_found")
    return found


def _tracked(db: Session, ctx: Context, item_id: uuid.UUID, quantity: Decimal) -> catalog.CatalogItemOut:
    item = catalog.get_items(db, ctx, {item_id}).get(item_id)
    if item is None:
        raise NotFound("catalog.item_not_found")
    if item.kind != "product" or not item.is_stock_tracked:
        raise Invalid("inventory.not_stock_tracked")
    if item.unit == "each" and quantity != quantity.to_integral_value():
        raise Invalid("inventory.fractional_quantity")
    return item


def _locked_level(db: Session, ctx: Context, item_id: uuid.UUID, location_id: uuid.UUID) -> StockLevel:
    """Create the level row on first use, then lock it: concurrent changes to one item serialise here."""
    assert ctx.business_id is not None
    db.execute(
        pg_insert(StockLevel)
        .values(
            id=new_id(),
            business_id=ctx.business_id,
            item_id=item_id,
            stock_location_id=location_id,
            on_hand=ZERO,
            reserved=ZERO,
        )
        .on_conflict_do_nothing()
    )
    level = db.scalar(
        select(StockLevel)
        .where(
            StockLevel.business_id == ctx.business_id,
            StockLevel.item_id == item_id,
            StockLevel.stock_location_id == location_id,
        )
        .with_for_update()
    )
    assert level is not None
    return level


def _rule(db: Session, ctx: Context, item_id: uuid.UUID, location_id: uuid.UUID) -> ReorderRule | None:
    return db.scalar(
        select(ReorderRule).where(
            ReorderRule.business_id == ctx.business_id,
            ReorderRule.item_id == item_id,
            ReorderRule.stock_location_id == location_id,
        )
    )


def _after_change(db: Session, ctx: Context, level: StockLevel, available_before: Decimal) -> None:
    """Publish the reorder event exactly when available crosses from >= point to < point."""
    rule = _rule(db, ctx, level.item_id, level.stock_location_id)
    available = level.on_hand - level.reserved
    if rule is not None and available_before >= rule.reorder_point > available:
        events.publish(
            db,
            ctx,
            StockBelowReorderLevel(
                item_id=level.item_id,
                stock_location_id=level.stock_location_id,
                available=available,
                reorder_point=rule.reorder_point,
            ),
        )


def record_movement(
    db: Session, ctx: Context, m: MovementIn, *, available_before: Decimal | None = None
) -> StockMovement:
    """Ledger entry + level update. Callers check permissions (direct API requires inventory.stock.adjust)."""
    if m.quantity == 0:
        raise Invalid("inventory.zero_quantity")
    item = _tracked(db, ctx, m.item_id, m.quantity)
    location_id = _location_id(db, ctx, m.stock_location_id)
    level = _locked_level(db, ctx, m.item_id, location_id)
    before = level.on_hand - level.reserved if available_before is None else available_before
    if level.on_hand + m.quantity < 0:
        raise Conflict(
            "inventory.insufficient_on_hand", item=item.name, on_hand=str(level.on_hand), requested=str(-m.quantity)
        )
    level.on_hand += m.quantity
    level.version += 1
    assert ctx.business_id is not None
    row = StockMovement(
        id=new_id(),
        business_id=ctx.business_id,
        item_id=m.item_id,
        stock_location_id=location_id,
        quantity=m.quantity,
        reason=m.reason,
        ref_type=m.ref_type,
        ref_id=m.ref_id,
        note=m.note,
        unit_cost_minor=m.unit_cost_minor,
        occurred_at=clock.now(),
        recorded_by=ctx.user_id,
        proposal_id=ctx.proposal_id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        ctx,
        action="inventory.movement.recorded",
        target_type="catalog_item",
        target_id=m.item_id,
        summary=f"{item.name}: {format_quantity(m.quantity, signed=True)} ({m.reason})",
        changes={"on_hand": {"from": str(level.on_hand - m.quantity), "to": str(level.on_hand)}},
    )
    _after_change(db, ctx, level, before)
    return row


def adjust(db: Session, ctx: Context, m: MovementIn) -> StockMovement:
    ctx.require(P.STOCK_ADJUST)
    if m.reason in ("consumption", "sale", "transfer_in", "transfer_out"):
        raise Invalid(
            "inventory.reason_not_allowed_here", detail="consumption and sales come from appointments and orders"
        )
    row = record_movement(db, ctx, m)
    events.publish(db, ctx, StockAdjusted(item_id=m.item_id, quantity=m.quantity, reason=m.reason))
    return row


def count(db: Session, ctx: Context, c: CountIn) -> StockMovement | None:
    ctx.require(P.STOCK_ADJUST)
    location_id = _location_id(db, ctx, c.stock_location_id)
    level = _locked_level(db, ctx, c.item_id, location_id)
    delta = c.counted - level.on_hand
    if delta == 0:
        return None
    return record_movement(
        db,
        ctx,
        MovementIn(
            item_id=c.item_id, quantity=delta, reason="count_correction", note=c.note, stock_location_id=location_id
        ),
    )


def reserve(
    db: Session,
    ctx: Context,
    *,
    item_id: uuid.UUID,
    quantity: Decimal,
    ref_type: str,
    ref_id: uuid.UUID,
    stock_location_id: uuid.UUID | None = None,
) -> StockReservation:
    if quantity <= 0:
        raise Invalid("inventory.zero_quantity")
    item = _tracked(db, ctx, item_id, quantity)
    location_id = _location_id(db, ctx, stock_location_id)
    level = _locked_level(db, ctx, item_id, location_id)
    available = level.on_hand - level.reserved
    if available < quantity and not org.get_settings(db, ctx).allow_backorders:
        raise Conflict(
            "inventory.insufficient_stock", item=item.name, available=str(available), requested=str(quantity)
        )
    level.reserved += quantity
    level.version += 1
    assert ctx.business_id is not None
    row = StockReservation(
        id=new_id(),
        business_id=ctx.business_id,
        item_id=item_id,
        stock_location_id=location_id,
        quantity=quantity,
        status="active",
        ref_type=ref_type,
        ref_id=ref_id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        ctx,
        action="inventory.reservation.created",
        target_type="catalog_item",
        target_id=item_id,
        summary=f"{item.name}: {format_quantity(quantity)} reserved for {ref_type}",
    )
    _after_change(db, ctx, level, available)
    return row


def _active(db: Session, ctx: Context, ref_type: str, ref_id: uuid.UUID) -> list[StockReservation]:
    return list(
        db.scalars(
            select(StockReservation)
            .where(
                StockReservation.business_id == ctx.business_id,
                StockReservation.ref_type == ref_type,
                StockReservation.ref_id == ref_id,
                StockReservation.status == "active",
            )
            .order_by(StockReservation.item_id, StockReservation.stock_location_id, StockReservation.id)
            .with_for_update()
        )
    )


def release(db: Session, ctx: Context, *, ref_type: str, ref_id: uuid.UUID) -> int:
    """Give promised stock back. No stock movement: nothing physically moved. Idempotent."""
    rows = _active(db, ctx, ref_type, ref_id)
    for r in rows:
        level = _locked_level(db, ctx, r.item_id, r.stock_location_id)
        level.reserved -= r.quantity
        level.version += 1
        r.status, r.released_at = "released", clock.now()
        audit.record(
            db,
            ctx,
            action="inventory.reservation.released",
            target_type="catalog_item",
            target_id=r.item_id,
            summary=f"{format_quantity(r.quantity)} released ({ref_type})",
        )
    db.flush()
    return len(rows)


def consume(
    db: Session,
    ctx: Context,
    *,
    ref_type: str,
    ref_id: uuid.UUID,
    reason: Literal["consumption", "sale"] = "consumption",
) -> int:
    """Parts were used (`consumption`) or sold (`sale`): one movement per reservation, and the reservation is closed."""
    rows = _active(db, ctx, ref_type, ref_id)
    for r in rows:
        level = _locked_level(db, ctx, r.item_id, r.stock_location_id)
        available_before = level.on_hand - level.reserved
        level.reserved -= r.quantity
        movement = record_movement(
            db,
            ctx,
            MovementIn(
                item_id=r.item_id,
                quantity=-r.quantity,
                reason=reason,
                stock_location_id=r.stock_location_id,
                ref_type=ref_type,
                ref_id=ref_id,
            ),
            available_before=available_before,
        )
        r.status, r.consumed_movement_id = "consumed", movement.id
    db.flush()
    return len(rows)


def availability(db: Session, ctx: Context, item_ids: list[uuid.UUID] | None = None) -> list[AvailabilityOut]:
    location = default_location(db, ctx)
    products = {p.id: p for p in catalog.list_stock_tracked(db, ctx)}
    wanted = [i for i in (item_ids or list(products)) if i in products]
    levels = {
        lv.item_id: lv
        for lv in db.scalars(
            select(StockLevel).where(
                StockLevel.business_id == ctx.business_id,
                StockLevel.stock_location_id == location.id,
                StockLevel.item_id.in_(wanted),
            )
        )
    }
    rules = {
        r.item_id: r
        for r in db.scalars(
            select(ReorderRule).where(
                ReorderRule.business_id == ctx.business_id,
                ReorderRule.stock_location_id == location.id,
                ReorderRule.item_id.in_(wanted),
            )
        )
    }
    out = []
    for item_id in wanted:
        p, lv, rule = products[item_id], levels.get(item_id), rules.get(item_id)
        on_hand, reserved = (lv.on_hand, lv.reserved) if lv else (ZERO, ZERO)
        available = on_hand - reserved
        out.append(
            AvailabilityOut(
                item_id=item_id,
                item_name=p.name,
                sku=p.sku,
                unit=p.unit,
                stock_location_id=location.id,
                on_hand=on_hand,
                reserved=reserved,
                available=available,
                reorder_point=rule.reorder_point if rule else None,
                below_reorder=bool(rule and available < rule.reorder_point),
            )
        )
    return out


def suggestions(db: Session, ctx: Context) -> list[ReorderSuggestion]:
    """Deterministic: suggested = ceil(target_level - available) for every item below its reorder point."""
    out = []
    location = default_location(db, ctx)
    for a in availability(db, ctx):
        if not a.below_reorder:
            continue
        rule = _rule(db, ctx, a.item_id, location.id)
        assert rule is not None
        suggested = Decimal(math.ceil(rule.target_level - a.available))
        out.append(
            ReorderSuggestion(
                item_id=a.item_id,
                item_name=a.item_name,
                available=a.available,
                reorder_point=rule.reorder_point,
                target_level=rule.target_level,
                suggested=suggested,
                explanation=(
                    f"Available {format_quantity(a.available)} is below the reorder point "
                    f"{format_quantity(rule.reorder_point)}; ordering {format_quantity(suggested)} "
                    f"brings it to the target level {format_quantity(rule.target_level)}."
                ),
            )
        )
    return out


def put_rule(db: Session, ctx: Context, item_id: uuid.UUID, data: ReorderRuleIn) -> ReorderRule:
    ctx.require(P.REORDER_WRITE)
    if data.target_level <= data.reorder_point:
        raise Invalid("inventory.invalid_rule", detail="target level must be above the reorder point")
    _tracked(db, ctx, item_id, Decimal(1))
    location_id = default_location(db, ctx).id
    rule = _rule(db, ctx, item_id, location_id)
    assert ctx.business_id is not None
    if rule is None:
        rule = ReorderRule(
            business_id=ctx.business_id, item_id=item_id, stock_location_id=location_id, **data.model_dump()
        )
        db.add(rule)
    else:
        for k, v in data.model_dump().items():
            setattr(rule, k, v)
        rule.version += 1
    db.flush()
    audit.record(
        db,
        ctx,
        action="inventory.reorder_rule.saved",
        target_type="catalog_item",
        target_id=item_id,
        summary=f"Reorder at {format_quantity(data.reorder_point)}, up to {format_quantity(data.target_level)}",
    )
    return rule


def has_open_reorder_draft(db: Session, ctx: Context, item_id: uuid.UUID, *, lock: bool = False) -> bool:
    """Open means not converted/cancelled. Optional item lock serializes check-and-create transactions."""
    if lock:
        db.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"open-reorder:{ctx.business_id}:{item_id}"},
        )
    return (
        db.scalar(
            select(ReorderDraftLine.id)
            .join(
                ReorderDraft,
                and_(
                    ReorderDraft.business_id == ReorderDraftLine.business_id,
                    ReorderDraft.id == ReorderDraftLine.draft_id,
                ),
            )
            .where(
                ReorderDraft.business_id == ctx.business_id,
                ReorderDraft.status == "draft",
                ReorderDraftLine.item_id == item_id,
            )
            .limit(1)
        )
        is not None
    )


def create_draft(db: Session, ctx: Context, lines: list[DraftLineIn], note: str | None) -> ReorderDraft:
    assert ctx.business_id is not None
    for item_id in sorted({line.item_id for line in lines}):
        if has_open_reorder_draft(db, ctx, item_id, lock=True):
            raise Conflict("inventory.open_reorder_draft")
    for line in lines:
        _tracked(db, ctx, line.item_id, line.quantity)
    draft = ReorderDraft(
        id=new_id(), business_id=ctx.business_id, status="draft", note=note, proposal_id=ctx.proposal_id
    )
    db.add(draft)
    db.flush()
    db.add_all(ReorderDraftLine(business_id=ctx.business_id, draft_id=draft.id, **ln.model_dump()) for ln in lines)
    db.flush()
    audit.record(
        db,
        ctx,
        action="inventory.reorder_draft.created",
        target_type="reorder_draft",
        target_id=draft.id,
        summary=f"Reorder draft with {len(lines)} line(s)",
    )
    return draft


def draft_lines(db: Session, ctx: Context, draft_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ReorderDraftLine]]:
    out: dict[uuid.UUID, list[ReorderDraftLine]] = {}
    for ln in db.scalars(
        select(ReorderDraftLine).where(
            ReorderDraftLine.business_id == ctx.business_id, ReorderDraftLine.draft_id.in_(draft_ids)
        )
    ):
        out.setdefault(ln.draft_id, []).append(ln)
    return out
