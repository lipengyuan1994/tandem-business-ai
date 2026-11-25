"""Invoice payment ledger: amounts received against ordinary invoices. Callers lock the invoice row first.

`invoices.amount_paid_minor` always equals the sum of the invoice's non-voided payments. Credit notes reduce what is
owed (`amount_credited_minor`), and money paid back to the customer (`amount_refunded_minor`) reduces what was paid.
The open balance is total - (paid - refunded) - credited; an issued invoice becomes `paid` exactly when it reaches
zero with some money received, and `void` when credited in full. Payments never exceed the open balance (no customer
credit balances yet).
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.context import Context
from app.platform.errors import Conflict, NotFound
from app.platform.refunds import refunds_covered

from .models import Invoice, InvoicePayment, InvoiceRefund
from .schemas import PaymentStatus


def net_paid(row: Invoice) -> int:
    return row.amount_paid_minor - row.amount_refunded_minor


def balance(row: Invoice) -> int:
    return row.total_minor - net_paid(row) - row.amount_credited_minor


def payment_status(row: Invoice) -> PaymentStatus | None:
    if row.kind != "invoice" or row.status not in ("issued", "paid"):
        return None
    if row.status == "paid":
        return "paid"
    return "partially_paid" if net_paid(row) > 0 else "unpaid"


def settle_after_credit(row: Invoice) -> None:
    """Status of a credited invoice: void when credited in full, paid when nothing is left to pay, else issued."""
    if row.amount_credited_minor == row.total_minor:
        row.status, row.paid_at = "void", None
    elif balance(row) == 0:
        if row.status != "paid":
            row.status, row.paid_at = "paid", clock.now()
    else:
        row.status, row.paid_at = "issued", None


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
    if balance(row) == 0:
        row.status = "paid"
        row.paid_at = clock.now()
    db.flush()
    return payment


def refund_dates_covered(
    db: Session,
    ctx: Context,
    invoice_id: uuid.UUID,
    *,
    extra: tuple[date, int] | None = None,
    exclude_payment: uuid.UUID | None = None,
) -> bool:
    paid = (
        (p.paid_on, p.amount_minor)
        for p in for_invoice(db, ctx, invoice_id)
        if p.voided_at is None and p.id != exclude_payment
    )
    refunded = [
        (r.paid_on, r.amount_minor)
        for r in db.scalars(
            select(InvoiceRefund).where(
                InvoiceRefund.business_id == ctx.business_id, InvoiceRefund.invoice_id == invoice_id
            )
        )
    ]
    if extra is not None:
        refunded.append(extra)
    return refunds_covered(paid, refunded)


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
    if row.amount_refunded_minor and not refund_dates_covered(db, ctx, row.id, exclude_payment=payment.id):
        raise Conflict(
            "sales.payment_refunded",
            detail="Part of this money was already paid back to the customer, so the payment cannot be voided",
        )
    payment.voided_at = clock.now()
    payment.version += 1
    row.amount_paid_minor -= payment.amount_minor
    if row.status == "paid" and balance(row) > 0:
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
