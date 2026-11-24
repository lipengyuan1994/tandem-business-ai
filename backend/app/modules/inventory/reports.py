"""Stock value report and average purchase costs, both read from the stock ledger.

The ledger is complete: levels change only through `service.record_movement`, so summing movements up to a moment
gives on-hand quantities at that moment. Costs come from inbound movements that carry `unit_cost_minor` (goods
receipts record the purchase-order price; manual receipts and gains may record one). Internal transfers are skipped.
There is no FIFO or perpetual cost layer: one weighted average purchase cost per item, as of the report date.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.org import public as org
from app.platform.clock import local_day_bounds
from app.platform.context import Context
from app.platform.reporting import local_today, round_minor

from . import permissions as P
from .models import StockLocation, StockMovement
from .schemas import StockValueReport, StockValueRow, StockValueTotals

COST_PERMISSION = "purchasing.po.read"
"""Costs are purchase prices, so cost-based reports also need purchasing read access."""

StockGroup = Literal["item", "location"]


def business_today(db: Session, ctx: Context) -> date:
    return local_today(org.get_business_brief(db, ctx).timezone)


def average_unit_costs(
    db: Session, ctx: Context, item_ids: set[uuid.UUID] | None, before: datetime
) -> dict[uuid.UUID, Decimal]:
    """Weighted average unit cost (exact minor units) of costed inbound movements before `before`, per item.

    Items with no costed inbound movement are absent. No permission check of its own; callers gate cost access.
    """
    if item_ids is not None and not item_ids:
        return {}
    stmt = (
        select(
            StockMovement.item_id,
            func.sum(StockMovement.quantity * StockMovement.unit_cost_minor),
            func.sum(StockMovement.quantity),
        )
        .where(
            StockMovement.business_id == ctx.business_id,
            StockMovement.quantity > 0,
            StockMovement.unit_cost_minor.is_not(None),
            StockMovement.reason != "transfer_in",
            StockMovement.occurred_at < before,
        )
        .group_by(StockMovement.item_id)
    )
    if item_ids is not None:
        stmt = stmt.where(StockMovement.item_id.in_(item_ids))
    return {item_id: Decimal(cost) / Decimal(qty) for item_id, cost, qty in db.execute(stmt) if qty > 0}


@dataclass
class _Group:
    label: str
    sku: str | None = None
    unit: str | None = None
    quantity: Decimal | None = None
    unit_cost: Decimal | None = None
    value: Decimal = Decimal(0)
    items: int = 0
    uncosted: int = 0


def stock_value(db: Session, ctx: Context, as_of: date, group_by: StockGroup) -> StockValueReport:
    """On hand at the end of `as_of` (business-local day), valued at average purchase cost as of then."""
    ctx.require(P.STOCK_READ)
    ctx.require(COST_PERMISSION)
    business = org.get_business_brief(db, ctx)
    end = local_day_bounds(as_of, business.timezone)[1]
    quantities = db.execute(
        select(StockMovement.item_id, StockMovement.stock_location_id, func.sum(StockMovement.quantity))
        .where(StockMovement.business_id == ctx.business_id, StockMovement.occurred_at < end)
        .group_by(StockMovement.item_id, StockMovement.stock_location_id)
        .having(func.sum(StockMovement.quantity) != 0)
    ).all()
    item_ids = {row[0] for row in quantities}
    items = catalog.get_items(db, ctx, item_ids)
    costs = average_unit_costs(db, ctx, item_ids, end)
    locations: dict[uuid.UUID, str] = {
        location_id: name
        for location_id, name in db.execute(
            select(StockLocation.id, StockLocation.name).where(StockLocation.business_id == ctx.business_id)
        )
    }
    groups: dict[uuid.UUID, _Group] = {}
    costed_items: set[uuid.UUID] = set()
    for item_id, location_id, quantity in quantities:
        item, cost = items[item_id], costs.get(item_id)
        key = item_id if group_by == "item" else location_id
        group = groups.get(key)
        if group is None:
            group = groups[key] = (
                _Group(label=item.name, sku=item.sku, unit=item.unit, quantity=Decimal(0), unit_cost=cost)
                if group_by == "item"
                else _Group(label=locations.get(location_id, ""))
            )
        if group.quantity is not None:
            group.quantity += quantity
        if cost is not None:
            group.value += quantity * cost
            costed_items.add(item_id)
        if group_by == "location" or group.items == 0:
            group.items += 1
            group.uncosted += cost is None
    rows = [
        StockValueRow(
            key=str(key),
            label=g.label,
            sku=g.sku,
            unit=g.unit,
            quantity=g.quantity,
            unit_cost_minor=None if g.unit_cost is None else round_minor(g.unit_cost),
            value_minor=round_minor(g.value),
            item_count=g.items,
            uncosted_item_count=g.uncosted,
        )
        for key, g in groups.items()
    ]
    rows.sort(key=lambda r: (-r.value_minor, r.label, r.key))
    return StockValueReport(
        as_of=as_of,
        group_by=group_by,
        currency=business.currency,
        rows=rows,
        totals=StockValueTotals(
            value_minor=sum(r.value_minor for r in rows),
            item_count=len(item_ids),
            uncosted_item_count=len(item_ids - costed_items),
        ),
    )
