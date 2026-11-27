"""Read-only feed of issued invoices, credit notes and customer payments for bookkeeping sync (accounting module).

Rows carry their `version`, which changes whenever the row changes, so a consumer can tell what is new without
comparing contents. Drafts are never part of the feed. Callers own the transaction.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.platform.context import Context

from . import permissions as P
from .models import Invoice, InvoiceLine, InvoicePayment, InvoiceRefund

MAX_IDS = 500


class LedgerLine(BaseModel):
    name: str
    quantity: Decimal
    unit_price_minor: int
    subtotal_minor: int
    tax_minor: int


class LedgerInvoice(BaseModel):
    id: uuid.UUID
    version: int
    kind: Literal["invoice", "credit_note"]
    status: str
    number: str
    customer_id: uuid.UUID
    bill_to_name: str
    bill_to_email: str | None
    issue_date: date
    due_date: date | None
    currency: str
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    credit_of_invoice_id: uuid.UUID | None
    refund_minor: int = 0
    """On a credit note: the part paid back to the customer rather than taken off what the invoice still owed."""
    refund_paid_on: date | None = None
    refund_method: str | None = None
    lines: list[LedgerLine]


class LedgerPayment(BaseModel):
    id: uuid.UUID
    version: int
    invoice_id: uuid.UUID
    customer_id: uuid.UUID
    currency: str
    amount_minor: int
    paid_on: date
    method: str
    reference: str | None
    voided: bool
    provider: str | None = None  # set for money taken online (M21), e.g. "stripe"


def invoice_versions(db: Session, ctx: Context, since: date) -> dict[uuid.UUID, int]:
    """Issued, paid or void invoices and credit notes dated on or after `since`: id -> version."""
    ctx.require(P.DOCUMENT_READ)
    rows = db.execute(
        select(Invoice.id, Invoice.version).where(
            Invoice.business_id == ctx.business_id, Invoice.status != "draft", Invoice.issue_date >= since
        )
    )
    return {row.id: row.version for row in rows}


def invoices(db: Session, ctx: Context, ids: set[uuid.UUID]) -> dict[uuid.UUID, LedgerInvoice]:
    ctx.require(P.DOCUMENT_READ)
    if not ids:
        return {}
    rows = list(
        db.scalars(
            select(Invoice).where(
                Invoice.business_id == ctx.business_id, Invoice.id.in_(list(ids)[:MAX_IDS]), Invoice.status != "draft"
            )
        )
    )
    lines: dict[uuid.UUID, list[LedgerLine]] = {row.id: [] for row in rows}
    if rows:
        for line in db.scalars(
            select(InvoiceLine)
            .where(InvoiceLine.business_id == ctx.business_id, InvoiceLine.document_id.in_(list(lines)))
            .order_by(InvoiceLine.document_id, InvoiceLine.position)
        ):
            lines[line.document_id].append(
                LedgerLine(
                    name=line.name,
                    quantity=line.quantity,
                    unit_price_minor=line.unit_price_minor,
                    subtotal_minor=line.line_subtotal_minor,
                    tax_minor=line.line_tax_minor,
                )
            )
    refunds = (
        {
            r.credit_note_id: r
            for r in db.scalars(
                select(InvoiceRefund).where(
                    InvoiceRefund.business_id == ctx.business_id, InvoiceRefund.credit_note_id.in_(list(lines))
                )
            )
        }
        if rows
        else {}
    )
    out: dict[uuid.UUID, LedgerInvoice] = {}
    for row in rows:
        if row.number is None or row.issue_date is None:
            continue
        out[row.id] = LedgerInvoice(
            id=row.id,
            version=row.version,
            kind="credit_note" if row.kind == "credit_note" else "invoice",
            status=row.status,
            number=row.number,
            customer_id=row.customer_id,
            bill_to_name=row.bill_to_name,
            bill_to_email=row.bill_to_email,
            issue_date=row.issue_date,
            due_date=row.due_date,
            currency=row.currency,
            subtotal_minor=row.subtotal_minor,
            tax_minor=row.tax_minor,
            total_minor=row.total_minor,
            credit_of_invoice_id=row.credit_of_invoice_id,
            refund_minor=refunds[row.id].amount_minor if row.id in refunds else 0,
            refund_paid_on=refunds[row.id].paid_on if row.id in refunds else None,
            refund_method=refunds[row.id].method if row.id in refunds else None,
            lines=lines[row.id],
        )
    return out


def payment_versions(db: Session, ctx: Context, since: date) -> dict[uuid.UUID, int]:
    """Payments received on or after `since`, or against an invoice issued on or after it (a payment can be dated
    before its invoice, such as a deposit), voided ones included: id -> version."""
    ctx.require(P.DOCUMENT_READ)
    rows = db.execute(
        select(InvoicePayment.id, InvoicePayment.version)
        .join(
            Invoice,
            (Invoice.business_id == InvoicePayment.business_id) & (Invoice.id == InvoicePayment.invoice_id),
        )
        .where(
            InvoicePayment.business_id == ctx.business_id,
            or_(InvoicePayment.paid_on >= since, Invoice.issue_date >= since),
        )
    )
    return {row.id: row.version for row in rows}


def payments(db: Session, ctx: Context, ids: set[uuid.UUID]) -> dict[uuid.UUID, LedgerPayment]:
    ctx.require(P.DOCUMENT_READ)
    if not ids:
        return {}
    rows = db.execute(
        select(InvoicePayment, Invoice.customer_id, Invoice.currency)
        .join(
            Invoice,
            (Invoice.business_id == InvoicePayment.business_id) & (Invoice.id == InvoicePayment.invoice_id),
        )
        .where(InvoicePayment.business_id == ctx.business_id, InvoicePayment.id.in_(list(ids)[:MAX_IDS]))
    ).all()
    return {
        payment.id: LedgerPayment(
            id=payment.id,
            version=payment.version,
            invoice_id=payment.invoice_id,
            customer_id=customer_id,
            currency=currency,
            amount_minor=payment.amount_minor,
            paid_on=payment.paid_on,
            method=payment.method,
            reference=payment.reference,
            voided=payment.voided_at is not None,
            provider=payment.provider,
        )
        for payment, customer_id, currency in rows
    }
