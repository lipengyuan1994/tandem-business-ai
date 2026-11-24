"""Invoice payment ledger: amounts received against ordinary invoices. Callers lock the invoice row first.

`invoices.amount_paid_minor` always equals the sum of the invoice's non-voided payments; an invoice is `paid` exactly
when that sum reaches its total. Payments never exceed the open balance (no customer credit balances yet).
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.context import Context
from app.platform.errors import Conflict, NotFound

from .models import Invoice, InvoicePayment
from .schemas import PaymentStatus


def balance(row: Invoice) -> int:
    return row.total_minor - row.amount_paid_minor


def payment_status(row: Invoice) -> PaymentStatus | None:
    if row.kind != "invoice" or row.status not in ("issued", "paid"):
        return None
    if row.status == "paid":
        return "paid"
    return "partially_paid" if row.amount_paid_minor > 0 else "unpaid"


def add(
    db: Session, ctx: Context, row: Invoice, amount_minor: int, paid_on: date, method: str, reference: str | None
) -> InvoicePayment | None:
    """Apply a payment to a locked, issued ordinary invoice. A zero amount (free invoice) only settles it."""
    if row.kind != "invoice" or row.status != "issued":
        raise Conflict("sales.invalid_transition", detail="Payments apply to issued invoices only")
    if amount_minor > balance(row):
        raise Conflict(
            "sales.overpayment",
            detail="The payment is larger than the open balance",
            balance_minor=balance(row),
        )
    payment = None
    if amount_minor > 0:
        payment = InvoicePayment(
            business_id=ctx.business_id,
            invoice_id=row.id,
            amount_minor=amount_minor,
            paid_on=paid_on,
            method=method,
            reference=reference,
        )
        db.add(payment)
    row.amount_paid_minor += amount_minor
    if row.amount_paid_minor == row.total_minor:
        row.status = "paid"
        row.paid_at = clock.now()
    db.flush()
    return payment


def void(db: Session, ctx: Context, row: Invoice, payment_id: uuid.UUID) -> InvoicePayment:
    """Void one payment of a locked invoice and reopen the invoice if it was paid."""
    payment = db.scalar(
        select(InvoicePayment)
        .where(
            InvoicePayment.business_id == ctx.business_id,
            InvoicePayment.invoice_id == row.id,
            InvoicePayment.id == payment_id,
        )
        .with_for_update()
    )
    if payment is None:
        raise NotFound("sales.payment_not_found")
    if payment.voided_at is not None:
        raise Conflict("sales.payment_already_voided")
    if row.status not in ("issued", "paid"):
        raise Conflict("sales.invalid_transition", detail=f"Cannot change payments of a {row.status} invoice")
    payment.voided_at = clock.now()
    payment.version += 1
    row.amount_paid_minor -= payment.amount_minor
    if row.status == "paid" and row.amount_paid_minor < row.total_minor:
        row.status = "issued"
        row.paid_at = None
    db.flush()
    return payment


def for_invoice(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[InvoicePayment]:
    return list(
        db.scalars(
            select(InvoicePayment)
            .where(InvoicePayment.business_id == ctx.business_id, InvoicePayment.invoice_id == invoice_id)
            .order_by(InvoicePayment.paid_on, InvoicePayment.created_at, InvoicePayment.id)
        )
    )
