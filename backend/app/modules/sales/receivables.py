"""Accounts receivable aging: what customers owe on issued invoices, by how late it is."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform.context import Context

from . import permissions as P
from .models import Invoice, InvoicePayment
from .schemas import AgingCustomer, AgingReport, AgingTotal

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
        return {**self.amounts, "total_minor": sum(self.amounts.values()), "invoice_count": self.count}


def aging(db: Session, ctx: Context, as_of: date) -> AgingReport:
    """Point in time: invoices issued by `as_of`, minus non-voided payments dated by `as_of`.

    Days past due count from the due date, or from the issue date when an invoice has none. Not yet due is current.
    Credited (void) invoices and credit notes are excluded.
    """
    ctx.require(P.DOCUMENT_READ)
    paid = (
        select(InvoicePayment.invoice_id, func.sum(InvoicePayment.amount_minor).label("paid_minor"))
        .where(
            InvoicePayment.business_id == ctx.business_id,
            InvoicePayment.voided_at.is_(None),
            InvoicePayment.paid_on <= as_of,
        )
        .group_by(InvoicePayment.invoice_id)
        .subquery()
    )
    open_minor = Invoice.total_minor - func.coalesce(paid.c.paid_minor, 0)
    rows = db.execute(
        select(
            Invoice.customer_id,
            Invoice.currency,
            Invoice.bill_to_name,
            Invoice.issue_date,
            func.coalesce(Invoice.due_date, Invoice.issue_date).label("reference_date"),
            open_minor.label("open_minor"),
        )
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .where(
            Invoice.business_id == ctx.business_id,
            Invoice.kind == "invoice",
            Invoice.status.in_(("issued", "paid")),
            Invoice.issue_date <= as_of,
            open_minor > 0,
        )
    ).all()
    customers: dict[tuple[uuid.UUID, str], _Sum] = {}
    totals: dict[str, _Sum] = {}
    for row in rows:
        days = (as_of - row.reference_date).days
        entry = customers.setdefault((row.customer_id, row.currency), _Sum())
        entry.add(days, int(row.open_minor))
        if row.issue_date >= entry.name_date:
            entry.name, entry.name_date = row.bill_to_name, row.issue_date
        totals.setdefault(row.currency, _Sum()).add(days, int(row.open_minor))
    ordered = sorted(customers.items(), key=lambda kv: (kv[0][1], -sum(kv[1].amounts.values()), kv[1].name))
    return AgingReport(
        as_of=as_of,
        customers=[
            AgingCustomer(customer_id=customer_id, customer_name=s.name, currency=currency, **s.fields())
            for (customer_id, currency), s in ordered
        ],
        totals=[AgingTotal(currency=currency, **totals[currency].fields()) for currency in sorted(totals)],
    )
