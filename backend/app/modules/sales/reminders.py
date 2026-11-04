"""Business-local overdue reads and manually approved reminders. No automatic sending."""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.errors import Conflict
from app.platform.money import minor_text, money_text
from app.platform.registry import ExecutionMeta, PreviewLine

from . import permissions as P
from . import repository as repo
from .models import Invoice
from .schemas import OverdueInvoice, PaymentReminderPayload
from .service import check_version


def business_today(db: Session, ctx: Context) -> date:
    return clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()


def list_overdue_invoices(
    db: Session, ctx: Context, *, as_of: date | None = None, limit: int = 50
) -> list[OverdueInvoice]:
    ctx.require(P.DOCUMENT_READ)
    today = as_of if as_of is not None else business_today(db, ctx)
    rows = db.scalars(
        select(Invoice)
        .where(
            Invoice.business_id == ctx.business_id,
            Invoice.kind == "invoice",
            Invoice.status == "issued",
            Invoice.due_date < today,
        )
        .order_by(Invoice.due_date, Invoice.id)
        .limit(max(0, limit))
    )
    return [
        OverdueInvoice(
            id=row.id,
            number=row.number or "",
            customer_id=row.customer_id,
            bill_to_name=row.bill_to_name,
            bill_to_email=row.bill_to_email,
            due_date=row.due_date,
            days_overdue=(today - row.due_date).days,
            total_minor=row.total_minor,
            currency=row.currency,
            version=row.version,
            last_reminder_at=row.last_reminder_at,
        )
        for row in rows
        if row.due_date is not None
    ]


def reminder_invoice(db: Session, ctx: Context, p: PaymentReminderPayload, *, lock: bool = False) -> Invoice:
    ctx.require(P.DOCUMENT_WRITE)
    row = repo.get(db, ctx, "invoice", p.invoice_id, lock=lock)
    assert isinstance(row, Invoice)
    check_version(row, p.invoice_version)
    if (
        row.kind != "invoice"
        or row.status != "issued"
        or row.due_date is None
        or row.due_date >= business_today(db, ctx)
    ):
        raise Conflict("sales.invoice_not_overdue")
    if not row.bill_to_email or not row.bill_to_email.strip():
        raise Conflict("sales.invoice_email_required")
    return row


def validate_reminder(db: Session, ctx: Context, p: PaymentReminderPayload) -> None:
    reminder_invoice(db, ctx, p)


def describe_reminder(db: Session, ctx: Context, p: PaymentReminderPayload) -> list[PreviewLine]:
    row = reminder_invoice(db, ctx, p)
    return [
        PreviewLine("Invoice", None, row.number),
        PreviewLine("Recipient", None, row.bill_to_email),
        PreviewLine("Message", None, p.body),
        PreviewLine("Amount due", None, money_text(row.total_minor, row.currency)),
        PreviewLine("Due date", None, str(row.due_date)),
    ]


def run_reminder(db: Session, ctx: Context, p: PaymentReminderPayload, meta: ExecutionMeta) -> dict[str, Any]:
    row = reminder_invoice(db, ctx, p, lock=True)
    outbox_id = notifications.enqueue_message(
        db,
        ctx,
        channel=notifications.email_channel(),
        recipient=row.bill_to_email,
        template_key="payment_reminder",
        variables={
            "number": row.number,
            "body": p.body,
            "amount": minor_text(row.total_minor, row.currency),
            "currency": row.currency,
            "due_date": str(row.due_date),
        },
        idempotency_key=f"sales.payment_reminder:{ctx.business_id}:{meta.idempotency_key}",
        related_type="sales.invoice",
        related_id=row.id,
        proposal_id=meta.proposal_id,
    )
    row.last_reminder_at = clock.now()
    row.version += 1
    db.flush()
    audit.record(
        db,
        ctx,
        action="sales.payment_reminder.sent",
        target_type="sales.invoice",
        target_id=row.id,
        summary="Invoice payment reminder queued",
        changes={"version": row.version, "last_reminder_at": row.last_reminder_at.isoformat()},
        proposal_id=meta.proposal_id,
    )
    return {"invoice_id": str(row.id), "outbox_id": str(outbox_id)}
