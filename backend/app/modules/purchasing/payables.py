"""Accounts payable aging: what the business owes suppliers on open bills, by how late it is.

The buckets match the receivables aging report (sales), so both sides of the books read the same way.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform.context import Context

from . import permissions as P
from .models import SupplierBill, SupplierBillPayment, SupplierCredit
from .schemas_bills import PayablesAgingReport, PayablesSupplier, PayablesTotal

BUCKETS = ("current_minor", "days_1_30_minor", "days_31_60_minor", "days_61_90_minor", "days_over_90_minor")


def bucket(days_past_due: int) -> str:
    if days_past_due <= 0:
        return "current_minor"
    if days_past_due <= 30:
        return "days_1_30_minor"
    if days_past_due <= 60:
        return "days_31_60_minor"
    if days_past_due <= 90:
        return "days_61_90_minor"
    return "days_over_90_minor"


@dataclass
class _Sum:
    name: str = ""
    name_date: date = date.min
    count: int = 0
    amounts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(BUCKETS, 0))

    def add(self, days_past_due: int, open_minor: int) -> None:
        self.amounts[bucket(days_past_due)] += open_minor
        self.count += 1

    def fields(self) -> dict[str, int]:
        return {**self.amounts, "total_minor": sum(self.amounts.values()), "bill_count": self.count}


def aging(db: Session, ctx: Context, as_of: date) -> PayablesAgingReport:
    """Point in time: bills dated by `as_of`, minus non-voided payments dated by `as_of` and live supplier credits dated
    by `as_of`, plus refunds by `as_of` whose credit was also dated by then.

    Days past due count from the due date, or from the bill date when a bill has none. Not yet due is current.
    Void bills are excluded.
    """
    ctx.require(P.BILL_READ)
    paid = (
        select(SupplierBillPayment.bill_id, func.sum(SupplierBillPayment.amount_minor).label("paid_minor"))
        .where(
            SupplierBillPayment.business_id == ctx.business_id,
            SupplierBillPayment.voided_at.is_(None),
            SupplierBillPayment.paid_on <= as_of,
        )
        .group_by(SupplierBillPayment.bill_id)
        .subquery()
    )
    credited = (
        select(SupplierCredit.bill_id, func.sum(SupplierCredit.amount_minor).label("credited_minor"))
        .where(
            SupplierCredit.business_id == ctx.business_id,
            SupplierCredit.voided_at.is_(None),
            SupplierCredit.credit_date <= as_of,
        )
        .group_by(SupplierCredit.bill_id)
        .subquery()
    )
    refunded = (
        select(SupplierCredit.bill_id, func.sum(SupplierCredit.refund_minor).label("refunded_minor"))
        .where(
            SupplierCredit.business_id == ctx.business_id,
            SupplierCredit.voided_at.is_(None),
            SupplierCredit.refund_received_on <= as_of,
            SupplierCredit.credit_date <= as_of,
        )
        .group_by(SupplierCredit.bill_id)
        .subquery()
    )
    # A legacy refund dated before the matching cash cannot add new debt to this report.
    open_minor = (
        SupplierBill.total_minor
        - func.coalesce(paid.c.paid_minor, 0)
        - func.coalesce(credited.c.credited_minor, 0)
        + func.least(func.coalesce(refunded.c.refunded_minor, 0), func.coalesce(paid.c.paid_minor, 0))
    )
    rows = db.execute(
        select(
            SupplierBill.supplier_id,
            SupplierBill.currency,
            SupplierBill.supplier_name,
            SupplierBill.bill_date,
            func.coalesce(SupplierBill.due_date, SupplierBill.bill_date).label("reference_date"),
            open_minor.label("open_minor"),
        )
        .outerjoin(paid, paid.c.bill_id == SupplierBill.id)
        .outerjoin(credited, credited.c.bill_id == SupplierBill.id)
        .outerjoin(refunded, refunded.c.bill_id == SupplierBill.id)
        .where(
            SupplierBill.business_id == ctx.business_id,
            SupplierBill.status != "void",
            SupplierBill.bill_date <= as_of,
            open_minor > 0,
        )
    ).all()
    suppliers: dict[tuple[uuid.UUID, str], _Sum] = {}
    totals: dict[str, _Sum] = {}
    for row in rows:
        days = (as_of - row.reference_date).days
        entry = suppliers.setdefault((row.supplier_id, row.currency), _Sum())
        entry.add(days, int(row.open_minor))
        if row.bill_date >= entry.name_date:
            entry.name, entry.name_date = row.supplier_name, row.bill_date
        totals.setdefault(row.currency, _Sum()).add(days, int(row.open_minor))
    ordered = sorted(suppliers.items(), key=lambda kv: (kv[0][1], -sum(kv[1].amounts.values()), kv[1].name))
    return PayablesAgingReport(
        as_of=as_of,
        suppliers=[
            PayablesSupplier(supplier_id=supplier_id, supplier_name=s.name, currency=currency, **s.fields())
            for (supplier_id, currency), s in ordered
        ],
        totals=[PayablesTotal(currency=currency, **totals[currency].fields()) for currency in sorted(totals)],
    )
