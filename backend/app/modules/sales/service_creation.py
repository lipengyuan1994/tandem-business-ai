"""Document creation, appointment conversion and accounting export."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.crm import public as crm
from app.modules.scheduling import public as scheduling
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid
from app.platform.money import minor_text

from . import permissions as P
from . import repository as repo
from . import service
from .calculations import prepare_lines, pricing_snapshot
from .models import Document, Invoice
from .schemas import DocumentCreate, DocumentType, LineIn, QuotePricing


def business_currency(db: Session, ctx: Context) -> str:
    """Catalog's pricing currency is the org default, including documents with only free-text lines."""
    return catalog.get_default_currency(db, ctx)


def create(
    db: Session,
    ctx: Context,
    family: DocumentType,
    data: DocumentCreate,
    *,
    expected_pricing: QuotePricing | None = None,
) -> Document:
    ctx.require(P.DOCUMENT_WRITE)
    customer = crm.get_customer_summary(db, ctx, data.customer_id)
    currency = business_currency(db, ctx)
    values = prepare_lines(db, ctx, data.lines, currency)
    if expected_pricing is not None and pricing_snapshot(currency, values) != expected_pricing:
        raise Conflict(
            "sales.price_changed", detail="Line prices, tax or totals changed; revise and approve the quote again"
        )
    row = repo.MODELS[family](
        business_id=ctx.business_id,
        customer_id=data.customer_id,
        bill_to_name=customer.display_name,
        bill_to_email=crm.get_customer_contact(db, ctx, data.customer_id, "email"),
        bill_to_address=data.bill_to_address,
        currency=currency,
        due_date=data.due_date,
        notes=data.notes,
    )
    db.add(row)
    db.flush()
    service.store_lines(db, ctx, family, row, values)
    service.record(db, ctx, family, row, "created")
    return row


def from_appointment(db: Session, ctx: Context, appointment_id: uuid.UUID) -> Document:
    appointment = scheduling.get_appointment(db, ctx, appointment_id)
    if appointment.status != "completed":
        raise Conflict("sales.invalid_transition", detail="Only a completed appointment can be invoiced")
    if repo.converted(db, ctx, "invoice", "source_appointment_id", appointment_id):
        raise Conflict("sales.already_converted")
    # Inventory's public API has no consumed-parts read. Do not infer parts from today's service recipe.
    data = DocumentCreate(
        customer_id=appointment.customer_id,
        lines=[LineIn(catalog_item_id=appointment.service_id, quantity=Decimal("1"))],
    )
    row = create(db, ctx, "invoice", data)
    assert isinstance(row, Invoice)
    row.source_appointment_id = appointment_id
    service.record(db, ctx, "invoice", row, "from_appointment")
    return row


def export_csv(db: Session, ctx: Context, start: date, end: date) -> str:
    if end < start:
        raise Invalid("sales.invalid_date_range")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["number", "kind", "date", "customer", "subtotal", "tax", "total", "currency", "status"])
    for row in repo.export_invoices(db, ctx, start, end):
        writer.writerow(
            [
                row.number,
                row.kind,
                row.issue_date,
                csv_text(row.bill_to_name),
                minor_text(row.subtotal_minor, row.currency),
                minor_text(row.tax_minor, row.currency),
                minor_text(row.total_minor, row.currency),
                row.currency,
                row.status,
            ]
        )
    return output.getvalue()


def csv_text(value: str) -> str:
    """Prevent a customer name from becoming a spreadsheet formula on import."""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value
