"""Sales and gross margin reports over issued invoices and credit notes.

Every invoice that was issued counts on its issue date, even if a credit note voided it later; the credit note counts
negatively on its own issue date. Past periods therefore keep their totals, and the net over all time is exact.
Amounts are net of tax unless named otherwise. Drafts never count.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.modules.catalog import public as catalog
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.platform.context import Context
from app.platform.reporting import DateRange, Period, basis_points, period_start, round_minor

from . import permissions as P
from .models import Invoice, InvoiceLine
from .schemas import MarginReport, MarginRow, MarginTotals, SalesReport, SalesRow, SalesTotal

SalesGroup = Literal["period", "customer", "item"]
MarginGroup = Literal["item", "kind"]
FREE_TEXT = "free_text"
"""Row key for lines typed in by hand rather than picked from the catalog."""

COUNTED = or_(
    and_(Invoice.kind == "invoice", Invoice.status.in_(("issued", "paid", "void"))),
    and_(Invoice.kind == "credit_note", Invoice.status == "issued"),
)
SIGN = case((Invoice.kind == "credit_note", -1), else_=1)


def _in_range(r: DateRange) -> ColumnElement[bool]:
    return and_(Invoice.issue_date >= r.date_from, Invoice.issue_date <= r.date_to)


@dataclass
class _Sales:
    label: str = ""
    label_date: date = date.min
    invoices: set[uuid.UUID] = field(default_factory=set)
    credits: set[uuid.UUID] = field(default_factory=set)
    quantity: Decimal | None = None
    net: int = 0
    tax: int = 0
    total: int = 0

    def add(self, doc: uuid.UUID, kind: str, net: int, tax: int, total: int) -> None:
        sign = -1 if kind == "credit_note" else 1
        (self.credits if kind == "credit_note" else self.invoices).add(doc)
        self.net, self.tax, self.total = self.net + sign * net, self.tax + sign * tax, self.total + sign * total

    def merge(self, other: _Sales) -> None:
        self.invoices |= other.invoices
        self.credits |= other.credits
        self.net, self.tax, self.total = self.net + other.net, self.tax + other.tax, self.total + other.total

    def counts(self) -> dict[str, int]:
        return {"invoice_count": len(self.invoices), "credit_note_count": len(self.credits)}


def sales(db: Session, ctx: Context, r: DateRange, group_by: SalesGroup, period: Period) -> SalesReport:
    ctx.require(P.DOCUMENT_READ)
    if group_by == "item":
        groups = _sales_by_item(db, ctx, r)
    else:
        groups = _sales_by_document(db, ctx, r, group_by, period)
    totals: dict[str, _Sales] = {}
    for (_, currency), g in groups.items():
        totals.setdefault(currency, _Sales()).merge(g)
    if group_by == "period":
        ordered = sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    else:
        ordered = sorted(groups.items(), key=lambda kv: (kv[0][1], -kv[1].net, kv[1].label, kv[0][0]))
    return SalesReport(
        date_from=r.date_from,
        date_to=r.date_to,
        group_by=group_by,
        period=period,
        rows=[
            SalesRow(
                key=key,
                label=g.label,
                currency=currency,
                quantity=g.quantity,
                net_minor=g.net,
                tax_minor=g.tax,
                total_minor=g.total,
                **g.counts(),
            )
            for (key, currency), g in ordered
        ],
        totals=[
            SalesTotal(currency=c, net_minor=t.net, tax_minor=t.tax, total_minor=t.total, **t.counts())
            for c, t in sorted(totals.items())
        ],
    )


def _sales_by_document(
    db: Session, ctx: Context, r: DateRange, group_by: SalesGroup, period: Period
) -> dict[tuple[str, str], _Sales]:
    rows = db.execute(
        select(
            Invoice.id,
            Invoice.kind,
            Invoice.customer_id,
            Invoice.bill_to_name,
            Invoice.issue_date,
            Invoice.currency,
            Invoice.subtotal_minor,
            Invoice.tax_minor,
            Invoice.total_minor,
        ).where(Invoice.business_id == ctx.business_id, COUNTED, _in_range(r))
    ).all()
    groups: dict[tuple[str, str], _Sales] = {}
    for row in rows:
        if group_by == "period":
            key = period_start(row.issue_date, period).isoformat()
            g = groups.setdefault((key, row.currency), _Sales(label=key))
        else:
            g = groups.setdefault((str(row.customer_id), row.currency), _Sales())
            if row.issue_date >= g.label_date:  # latest bill-to name, as on the receivables report
                g.label, g.label_date = row.bill_to_name, row.issue_date
        g.add(row.id, row.kind, row.subtotal_minor, row.tax_minor, row.total_minor)
    return groups


def _sales_by_item(db: Session, ctx: Context, r: DateRange) -> dict[tuple[str, str], _Sales]:
    rows = db.execute(
        select(
            InvoiceLine.catalog_item_id,
            Invoice.id,
            Invoice.kind,
            Invoice.currency,
            func.sum(InvoiceLine.quantity),
            func.sum(InvoiceLine.line_subtotal_minor),
            func.sum(InvoiceLine.line_tax_minor),
            func.sum(InvoiceLine.line_total_minor),
        )
        .join(Invoice, and_(Invoice.business_id == InvoiceLine.business_id, Invoice.id == InvoiceLine.document_id))
        .where(Invoice.business_id == ctx.business_id, COUNTED, _in_range(r))
        .group_by(InvoiceLine.catalog_item_id, Invoice.id, Invoice.kind, Invoice.currency)
    ).all()
    names = {k: v.name for k, v in catalog.get_items(db, ctx, {row[0] for row in rows if row[0]}).items()}
    groups: dict[tuple[str, str], _Sales] = {}
    for item_id, doc, kind, currency, quantity, net, tax, total in rows:
        key = str(item_id) if item_id else FREE_TEXT
        g = groups.setdefault(
            (key, currency), _Sales(label=names.get(item_id, "") if item_id else "", quantity=Decimal(0))
        )
        g.add(doc, kind, int(net), int(tax), int(total))
        assert g.quantity is not None
        g.quantity += -quantity if kind == "credit_note" else quantity
    return groups


@dataclass
class _Margin:
    label: str
    kind: str | None = None
    quantity: Decimal | None = None
    revenue: int = 0
    costed_revenue: int = 0
    cost: Decimal = Decimal(0)
    costed: bool = False

    def row(self, key: str) -> MarginRow:
        cost = round_minor(self.cost)
        has_cost = self.costed
        return MarginRow(
            key=key,
            label=self.label,
            kind=self.kind,
            quantity=self.quantity,
            revenue_minor=self.revenue,
            costed_revenue_minor=self.costed_revenue,
            uncosted_revenue_minor=self.revenue - self.costed_revenue,
            cost_minor=cost if has_cost else None,
            margin_minor=self.costed_revenue - cost if has_cost else None,
            margin_basis_points=basis_points(self.costed_revenue - cost, self.costed_revenue) if has_cost else None,
        )


def margin(db: Session, ctx: Context, r: DateRange, group_by: MarginGroup) -> MarginReport:
    """Gross margin on net sales in the business currency, at weighted average purchase cost as of `date_to`.

    Products cost their average purchase cost; services cost their current parts (materials only, no labour).
    Lines without a known cost (free text, never-received products, services without costed parts) are revenue
    without cost: they appear in `uncosted_revenue_minor` and are left out of margin.
    """
    ctx.require(P.DOCUMENT_READ)
    ctx.require(inventory.COST_PERMISSION)
    business = org.get_business_brief(db, ctx)
    rows = db.execute(
        select(
            InvoiceLine.catalog_item_id,
            func.sum(SIGN * InvoiceLine.quantity),
            func.sum(SIGN * InvoiceLine.line_subtotal_minor),
        )
        .join(Invoice, and_(Invoice.business_id == InvoiceLine.business_id, Invoice.id == InvoiceLine.document_id))
        .where(Invoice.business_id == ctx.business_id, COUNTED, Invoice.currency == business.currency, _in_range(r))
        .group_by(InvoiceLine.catalog_item_id)
    ).all()
    other_currency = db.scalar(
        select(func.count())
        .select_from(Invoice)
        .where(Invoice.business_id == ctx.business_id, COUNTED, Invoice.currency != business.currency, _in_range(r))
    )
    item_ids = {row[0] for row in rows if row[0]}
    items = catalog.get_items(db, ctx, item_ids)
    parts = catalog.get_parts_by_service(db, ctx, {i for i, x in items.items() if x.kind == "service"})
    wanted = item_ids | {p.item_id for ps in parts.values() for p in ps}
    costs = inventory.average_unit_costs(db, ctx, wanted, r.utc_bounds(business.timezone)[1])
    unit_costs = {i: _unit_cost(x.kind, i, parts, costs) for i, x in items.items()}
    groups: dict[str, _Margin] = {}
    total = _Margin(label="")
    for item_id, quantity, revenue in rows:
        item = items.get(item_id) if item_id else None
        kind = item.kind if item else FREE_TEXT
        if group_by == "item":
            key = str(item_id) if item_id else FREE_TEXT
            g = groups.setdefault(key, _Margin(label=item.name if item else "", kind=kind, quantity=Decimal(0)))
            assert g.quantity is not None
            g.quantity += quantity
        else:
            g = groups.setdefault(kind, _Margin(label=kind, kind=kind))
        unit_cost = unit_costs.get(item_id) if item_id else None
        for target in (g, total):
            target.revenue += int(revenue)
            if unit_cost is not None:
                target.costed = True
                target.costed_revenue += int(revenue)
                target.cost += quantity * unit_cost
    ordered = sorted(groups.items(), key=lambda kv: (-kv[1].revenue, kv[1].label, kv[0]))
    t = total.row("total")
    return MarginReport(
        date_from=r.date_from,
        date_to=r.date_to,
        group_by=group_by,
        currency=business.currency,
        rows=[g.row(key) for key, g in ordered],
        totals=MarginTotals(**t.model_dump(include=set(MarginTotals.model_fields))),
        other_currency_document_count=int(other_currency or 0),
    )


def _unit_cost(
    kind: str,
    item_id: uuid.UUID,
    parts: dict[uuid.UUID, list[catalog.ServicePartOut]],
    costs: dict[uuid.UUID, Decimal],
) -> Decimal | None:
    if kind == "product":
        return costs.get(item_id)
    service_parts = parts.get(item_id, [])
    if not service_parts or any(p.item_id not in costs for p in service_parts):
        return None
    return sum((p.quantity * costs[p.item_id] for p in service_parts), Decimal(0))
