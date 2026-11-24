"""Spend report: what was ordered from and received from suppliers.

`ordered` counts purchase orders on their approval date (cancelled ones excluded); `received` values goods receipts
at their purchase-order unit cost on the business-local day they were recorded. Supplier bills and payments are not
recorded in Tandem, so received value is the best measure of spend it has.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform.context import Context
from app.platform.reporting import DateRange, Period, period_start, round_minor

from . import permissions as P
from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine
from .schemas import SpendReport, SpendRow, SpendTotal

SpendGroup = Literal["supplier", "period"]


@dataclass
class _Spend:
    label: str = ""
    label_date: date = date.min
    orders: set[uuid.UUID] = field(default_factory=set)
    receipts: set[uuid.UUID] = field(default_factory=set)
    ordered: int = 0
    received: Decimal = Decimal(0)

    def named(self, name: str, on: date) -> None:
        if on >= self.label_date:  # latest supplier name on a document in range
            self.label, self.label_date = name, on

    def merge(self, other: _Spend) -> None:
        self.orders |= other.orders
        self.receipts |= other.receipts
        self.ordered += other.ordered
        self.received += other.received

    def amounts(self) -> dict[str, int]:
        return {
            "order_count": len(self.orders),
            "ordered_minor": self.ordered,
            "receipt_count": len(self.receipts),
            "received_minor": round_minor(self.received),
        }


def spend(db: Session, ctx: Context, r: DateRange, group_by: SpendGroup, period: Period) -> SpendReport:
    ctx.require(P.READ)
    tz = org.get_business_brief(db, ctx).timezone
    groups: dict[tuple[str, str], _Spend] = {}

    def group(supplier_id: uuid.UUID, on: date, currency: str, name: str) -> _Spend:
        key = str(supplier_id) if group_by == "supplier" else period_start(on, period).isoformat()
        g = groups.setdefault((key, currency), _Spend(label=key))
        if group_by == "supplier":
            g.named(name, on)
        return g

    orders = db.execute(
        select(
            PurchaseOrder.id,
            PurchaseOrder.supplier_id,
            PurchaseOrder.supplier_name,
            PurchaseOrder.currency,
            PurchaseOrder.approved_date,
            PurchaseOrder.total_minor,
        ).where(
            PurchaseOrder.business_id == ctx.business_id,
            PurchaseOrder.status.not_in(("draft", "cancelled")),
            PurchaseOrder.approved_date >= r.date_from,
            PurchaseOrder.approved_date <= r.date_to,
        )
    ).all()
    for o in orders:
        g = group(o.supplier_id, o.approved_date, o.currency, o.supplier_name)
        g.orders.add(o.id)
        g.ordered += o.total_minor

    start, end = r.utc_bounds(tz)
    zone = ZoneInfo(tz)
    lines = db.execute(
        select(
            GoodsReceipt.id,
            GoodsReceipt.created_at,
            PurchaseOrder.supplier_id,
            PurchaseOrder.supplier_name,
            PurchaseOrder.currency,
            GoodsReceiptLine.quantity,
            PurchaseOrderLine.unit_cost_minor,
        )
        .join(
            GoodsReceiptLine,
            and_(
                GoodsReceiptLine.business_id == GoodsReceipt.business_id, GoodsReceiptLine.receipt_id == GoodsReceipt.id
            ),
        )
        .join(
            PurchaseOrderLine,
            and_(
                PurchaseOrderLine.business_id == GoodsReceiptLine.business_id,
                PurchaseOrderLine.id == GoodsReceiptLine.po_line_id,
            ),
        )
        .join(
            PurchaseOrder,
            and_(
                PurchaseOrder.business_id == GoodsReceipt.business_id,
                PurchaseOrder.id == GoodsReceipt.purchase_order_id,
            ),
        )
        .where(
            GoodsReceipt.business_id == ctx.business_id, GoodsReceipt.created_at >= start, GoodsReceipt.created_at < end
        )
    ).all()
    for line in lines:
        g = group(line.supplier_id, line.created_at.astimezone(zone).date(), line.currency, line.supplier_name)
        g.receipts.add(line.id)
        g.received += line.quantity * line.unit_cost_minor

    totals: dict[str, _Spend] = {}
    for (_, currency), g in groups.items():
        totals.setdefault(currency, _Spend()).merge(g)
    if group_by == "period":
        ordered = sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    else:
        ordered = sorted(
            groups.items(), key=lambda kv: (kv[0][1], -kv[1].received, -kv[1].ordered, kv[1].label, kv[0][0])
        )
    return SpendReport(
        date_from=r.date_from,
        date_to=r.date_to,
        group_by=group_by,
        period=period,
        rows=[SpendRow(key=key, label=g.label, currency=c, **g.amounts()) for (key, c), g in ordered],
        totals=[SpendTotal(currency=c, **t.amounts()) for c, t in sorted(totals.items())],
    )
