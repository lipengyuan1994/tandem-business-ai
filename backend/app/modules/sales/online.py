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
from app.platform.context import Context
from app.platform.errors import NotFound

from . import payments
from . import permissions as P
from . import repository as repo
from .models import Invoice, InvoicePayment
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


def _get(db: Session, ctx: Context, invoice_id: uuid.UUID, *, lock: bool = False) -> Invoice:
    try:
        row = repo.get(db, ctx, "invoice", invoice_id, lock=lock)
    except NotFound:
        raise NotFound("sales.document_not_found") from None
    assert isinstance(row, Invoice)
    if row.kind != "invoice":
        raise NotFound("sales.document_not_found")
    return row


def online_invoice(db: Session, ctx: Context, invoice_id: uuid.UUID) -> OnlineInvoice:
    """What a pay page or a pay-link check needs. The caller has already decided the reader may see it (a staff
    permission, or a signed pay link for this invoice)."""
    row = _get(db, ctx, invoice_id)
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
