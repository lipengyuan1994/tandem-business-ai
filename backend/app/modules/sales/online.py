"""Invoices paid online (M21): what a pay page may show, and recording money a payment provider took.

The payments module calls these. Recording is idempotent on (provider, external id), never pushes an invoice past
zero, and reports any surplus (money taken after the invoice was already settled) for the business to pay back or
keep. Card details never reach Tandem; only the provider's payment id does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform import clock
from app.platform.context import Context
from app.platform.errors import Conflict, NotFound

from . import payments
from . import permissions as P
from . import repository as repo
from .models import Invoice, InvoicePayment, InvoiceRefund
from .service import record

PROVIDERS = frozenset({"stripe"})
ONLINE_METHODS = frozenset({"card", "bank_transfer"})


@dataclass(frozen=True, slots=True)
class OnlineInvoice:
    id: uuid.UUID
    number: str | None
    status: str  # draft | issued | paid | void (credit notes are never offered online)
    currency: str
    total_minor: int
    balance_minor: int
    issue_date: date | None
    due_date: date | None
    customer_id: uuid.UUID
    bill_to_name: str


@dataclass(frozen=True, slots=True)
class OnlinePayment:
    payment_id: uuid.UUID | None
    applied_minor: int
    surplus_minor: int
    already_recorded: bool


@dataclass(frozen=True, slots=True)
class OnlineRefund:
    """Money a credit note says is owed back to the customer, which the payments module may pay out by card."""

    id: uuid.UUID
    credit_note_id: uuid.UUID
    credit_note_number: str | None
    amount_minor: int
    paid_on: date
    method: str


def _get(db: Session, ctx: Context, invoice_id: uuid.UUID, *, lock: bool = False) -> Invoice:
    try:
        row = repo.get(db, ctx, "invoice", invoice_id, lock=lock)
    except NotFound:
        raise NotFound("sales.document_not_found") from None
    assert isinstance(row, Invoice)
    if row.kind != "invoice":
        raise NotFound("sales.document_not_found")
    return row


def online_invoice(db: Session, ctx: Context, invoice_id: uuid.UUID, *, lock: bool = False) -> OnlineInvoice:
    """What a pay page or a pay-link check needs. The caller has already decided the reader may see it (a staff
    permission, or a signed pay link for this invoice). `lock` holds the invoice row until the caller's transaction
    ends, so a payment or credit cannot change the balance between this read and what the caller writes."""
    row = _get(db, ctx, invoice_id, lock=lock)
    return OnlineInvoice(
        id=row.id,
        number=row.number,
        status=row.status,
        currency=row.currency,
        total_minor=row.total_minor,
        balance_minor=payments.balance(row) if row.status in ("issued", "paid") else 0,
        issue_date=row.issue_date,
        due_date=row.due_date,
        customer_id=row.customer_id,
        bill_to_name=row.bill_to_name,
    )


def record_online_payment(
    db: Session,
    ctx: Context,
    invoice_id: uuid.UUID,
    *,
    amount_minor: int,
    paid_at: datetime,
    provider: str,
    external_id: str,
    method: str = "card",
) -> OnlinePayment:
    """Put money taken online on the invoice, once. What the invoice cannot take is returned as surplus.
    `paid_at` is when the provider took the money; the payment date is that day in the business's time zone."""
    ctx.require(P.MARK_PAID)
    if provider not in PROVIDERS or method not in ONLINE_METHODS or amount_minor <= 0 or not external_id:
        raise ValueError("invalid online payment")
    row = _get(db, ctx, invoice_id, lock=True)
    existing = db.scalar(
        select(InvoicePayment).where(
            InvoicePayment.business_id == ctx.business_id,
            InvoicePayment.provider == provider,
            InvoicePayment.external_id == external_id,
        )
    )
    if existing is not None:
        return OnlinePayment(existing.id, existing.amount_minor, amount_minor - existing.amount_minor, True)
    applied = min(amount_minor, payments.balance(row)) if row.status == "issued" else 0
    if applied == 0:
        return OnlinePayment(None, 0, amount_minor, False)
    paid_on = paid_at.astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()
    payment = payments.add(db, ctx, row, applied, paid_on, method, f"Paid online ({provider})")
    assert payment is not None
    payment.provider, payment.external_id = provider, external_id
    row.version += 1
    record(
        db,
        ctx,
        "invoice",
        row,
        "payment_recorded",
        payment_id=str(payment.id),
        amount_minor=applied,
        method=method,
        paid_on=paid_on.isoformat(),
        source=provider,
    )
    return OnlinePayment(payment.id, applied, amount_minor - applied, False)


def online_refunds(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[OnlineRefund]:
    """The invoice's refunds (one per credit note larger than what was still owed), oldest first."""
    rows = db.execute(
        select(InvoiceRefund, Invoice.number)
        .join(
            Invoice, (Invoice.business_id == InvoiceRefund.business_id) & (Invoice.id == InvoiceRefund.credit_note_id)
        )
        .where(InvoiceRefund.business_id == ctx.business_id, InvoiceRefund.invoice_id == invoice_id)
        .order_by(InvoiceRefund.paid_on, InvoiceRefund.created_at, InvoiceRefund.id)
    ).all()
    return [OnlineRefund(r.id, r.credit_note_id, number, r.amount_minor, r.paid_on, r.method) for r, number in rows]


def reverse_online_payment(
    db: Session,
    ctx: Context,
    invoice_id: uuid.UUID,
    *,
    provider: str,
    external_id: str,
    amount_minor: int,
    reason: str,
) -> int:
    """Take money a provider took back (a lost dispute) off the invoice, and reopen it. Returns what came off.

    The online payment is voided and whatever the provider let the business keep is recorded again on the same
    date (external id `<id>#kept`), so refunds already paid out stay covered by dated payments. Never takes off
    more than the invoice's net payments, so the ledger's settlement checks always hold. Calling it twice for the
    same payment changes nothing the second time.
    """
    ctx.require(P.MARK_PAID)
    if provider not in PROVIDERS or amount_minor <= 0 or not external_id:
        raise ValueError("invalid reversal")
    row = _get(db, ctx, invoice_id, lock=True)
    payment = db.scalar(
        select(InvoicePayment)
        .where(
            InvoicePayment.business_id == ctx.business_id,
            InvoicePayment.invoice_id == row.id,
            InvoicePayment.provider == provider,
            InvoicePayment.external_id == external_id,
        )
        .with_for_update()
    )
    if payment is None or payment.voided_at is not None:
        return 0  # nothing of this payment reached the invoice (all surplus), or it was reversed already
    if row.status not in ("issued", "paid"):
        raise Conflict("sales.invalid_transition", detail=f"Cannot change payments of a {row.status} invoice")
    taken = min(amount_minor, payment.amount_minor, payments.net_paid(row))
    if taken == 0:
        return 0
    kept = payment.amount_minor - taken
    payment.voided_at = clock.now()
    payment.version += 1
    row.amount_paid_minor -= payment.amount_minor
    if kept:
        again = InvoicePayment(
            business_id=ctx.business_id,
            invoice_id=row.id,
            amount_minor=kept,
            paid_on=payment.paid_on,
            method=payment.method,
            reference=payment.reference,
            provider=provider,
            external_id=f"{external_id}#kept"[:80],
        )
        db.add(again)
        row.amount_paid_minor += kept
    if row.status == "paid" and payments.balance(row) > 0:
        row.status, row.paid_at = "issued", None
    db.flush()
    if row.amount_refunded_minor and not payments.refund_dates_covered(db, ctx, row.id):
        raise Conflict(
            "sales.refund_exceeds_dated_payments",
            detail="Recorded payments would no longer cover the refunds already paid back",
        )
    row.version += 1
    record(
        db,
        ctx,
        "invoice",
        row,
        "payment_reversed",
        payment_id=str(payment.id),
        amount_minor=taken,
        source=provider,
        reason=reason,
    )
    return taken
