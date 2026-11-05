"""Public sales API: caller-owned transaction, no commits or external delivery."""

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.platform.context import Context

from .events import DocumentChanged
from .reminders import list_overdue_invoices
from .schemas import DocumentCreate, DocumentOut, DocumentUpdate, LineIn, OverdueInvoice
from .service import convert_quote, credit, detail, invoice_order, transition, update
from .service_creation import create, from_appointment

__all__ = [
    "DocumentChanged",
    "DocumentCreate",
    "DocumentOut",
    "DocumentUpdate",
    "LineIn",
    "OverdueInvoice",
    "convert_quote",
    "create",
    "credit",
    "detail",
    "from_appointment",
    "get_overdue_invoice",
    "invoice_order",
    "list_overdue_invoices",
    "overdue_invoice_page",
    "transition",
    "update",
]


def get_overdue_invoice(
    db: Session, ctx: Context, invoice_id: uuid.UUID, *, lock: bool = False, as_of: date | None = None
) -> OverdueInvoice | None:
    """Scoped point read; an optional row lock lasts until the caller's transaction ends."""
    from sqlalchemy import select

    from .models import Invoice
    from .reminders import business_today

    ctx.require("sales.document.read")
    today = as_of if as_of is not None else business_today(db, ctx)
    stmt = select(Invoice).where(Invoice.business_id == ctx.business_id, Invoice.id == invoice_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None or row.kind != "invoice" or row.status != "issued" or row.due_date is None or row.due_date >= today:
        return None
    return OverdueInvoice(
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


def overdue_invoice_page(
    db: Session, ctx: Context, *, after: tuple[date, uuid.UUID] | None = None, limit: int = 500
) -> list[OverdueInvoice]:
    """Bounded keyset page for non-inference scans; use (due_date, id) as the next cursor."""
    from sqlalchemy import select, tuple_

    from .models import Invoice
    from .reminders import business_today

    ctx.require("sales.document.read")
    today = business_today(db, ctx)
    stmt = select(Invoice).where(
        Invoice.business_id == ctx.business_id,
        Invoice.kind == "invoice",
        Invoice.status == "issued",
        Invoice.due_date < today,
    )
    if after is not None:
        stmt = stmt.where(tuple_(Invoice.due_date, Invoice.id) > after)
    rows = db.scalars(stmt.order_by(Invoice.due_date, Invoice.id).limit(max(0, min(limit, 500))))
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
