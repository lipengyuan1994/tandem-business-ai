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


def care_invoice_reference(
    db: Session, ctx: Context, customer_id: uuid.UUID, appointment_id: uuid.UUID | None = None
) -> uuid.UUID | None:
    """Issued invoice for the appointment; latest customer invoice only when no appointment is given."""
    from sqlalchemy import select

    from .models import Invoice

    ctx.require("sales.document.read")
    stmt = select(Invoice.id).where(
        Invoice.business_id == ctx.business_id,
        Invoice.customer_id == customer_id,
        Invoice.kind == "invoice",
        Invoice.status != "draft",
    )
    if appointment_id:
        exact = db.scalar(
            stmt.where(Invoice.source_appointment_id == appointment_id)
            .order_by(Invoice.created_at.desc(), Invoice.id.desc())
            .limit(1)
        )
        return exact
    return db.scalar(stmt.order_by(Invoice.created_at.desc(), Invoice.id.desc()).limit(1))


def validate_care_invoice(
    db: Session, ctx: Context, invoice_id: uuid.UUID, customer_id: uuid.UUID, appointment_id: uuid.UUID | None
) -> None:
    """Do not expose an invoice identifier as evidence for another tenant/customer/appointment."""
    from sqlalchemy import select

    from app.platform.errors import Invalid

    from .models import Invoice

    ctx.require("sales.document.read")
    row = db.scalar(select(Invoice).where(Invoice.business_id == ctx.business_id, Invoice.id == invoice_id))
    if (
        row is None
        or row.customer_id != customer_id
        or (appointment_id is not None and row.source_appointment_id != appointment_id)
    ):
        raise Invalid("customer_care.invoice_unavailable")
