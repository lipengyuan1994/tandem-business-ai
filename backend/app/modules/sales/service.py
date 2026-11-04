"""Shared staff/executor document operations. The caller owns the transaction; services never commit."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.platform import audit, clock, events
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid

from . import permissions as P
from . import repository as repo
from .calculations import prepare_lines
from .events import DocumentChanged
from .models import Document, Invoice, SalesOrder
from .schemas import DocumentOut, DocumentType, DocumentUpdate, LineOut

FLOWS: dict[DocumentType, dict[str, tuple[str, str]]] = {
    "quote": {
        "send": ("draft", "sent"),
        "accept": ("sent", "accepted"),
        "decline": ("sent", "declined"),
        "expire": ("sent", "expired"),
    },
    "order": {
        "confirm": ("draft", "confirmed"),
        "fulfil": ("confirmed", "fulfilled"),
        "cancel": ("confirmed", "cancelled"),
    },
    "invoice": {"issue": ("draft", "issued"), "mark-paid": ("issued", "paid")},
}


def check_version(row: Document, expected: int) -> None:
    if row.version != expected:
        raise Conflict("sales.version_conflict", current_version=row.version)


def require_status(row: Document, expected: str) -> None:
    if row.status != expected:
        raise Conflict("sales.invalid_transition", detail=f"Expected {expected}; document is {row.status}")


def record(db: Session, ctx: Context, family: DocumentType, row: Document, action: str) -> None:
    db.flush()
    audit.record(
        db,
        ctx,
        action=f"sales.{family}.{action}",
        target_type=f"sales.{family}",
        target_id=row.id,
        summary=f"Sales {family}: {action}",
        changes={"status": row.status, "version": row.version},
    )
    events.publish(
        db,
        ctx,
        DocumentChanged(
            document_id=row.id, document_type=family, action=action, status=row.status, version=row.version
        ),
    )


def store_lines(db: Session, ctx: Context, family: DocumentType, row: Document, values: list[dict[str, Any]]) -> None:
    for data in values:
        db.add(repo.LINES[family](business_id=ctx.business_id, document_id=row.id, **data))
    row.subtotal_minor = sum(line["line_subtotal_minor"] for line in values)
    row.tax_minor = sum(line["line_tax_minor"] for line in values)
    row.total_minor = row.subtotal_minor + row.tax_minor
    db.flush()


def detail(db: Session, ctx: Context, family: DocumentType, row: Document) -> DocumentOut:
    out = DocumentOut.model_validate(row)
    out.lines = [LineOut.model_validate(line) for line in repo.lines(db, ctx, family, row.id)]
    return out


def update(db: Session, ctx: Context, family: DocumentType, row_id: uuid.UUID, data: DocumentUpdate) -> Document:
    ctx.require(P.DOCUMENT_WRITE)
    row = repo.get(db, ctx, family, row_id, lock=True)
    if row.status != "draft":
        raise Conflict("sales.document_locked")
    check_version(row, data.version)
    if data.lines is not None:
        values = prepare_lines(db, ctx, data.lines, row.currency)
        repo.delete_lines(db, ctx, family, row_id)
        store_lines(db, ctx, family, row, values)
    for field in ("due_date", "notes"):
        if field in data.model_fields_set:
            setattr(row, field, getattr(data, field))
    row.version += 1
    record(db, ctx, family, row, "updated")
    return row


def transition(
    db: Session,
    ctx: Context,
    family: DocumentType,
    row_id: uuid.UUID,
    version: int,
    action: str,
) -> Document:
    ctx.require(P.MARK_PAID if action == "mark-paid" else P.DOCUMENT_ISSUE)
    row = repo.get(db, ctx, family, row_id, lock=True)
    check_version(row, version)
    flow = FLOWS[family].get(action)
    if flow is None:
        raise Conflict("sales.invalid_transition")
    require_status(row, flow[0])
    if action == "mark-paid" and isinstance(row, Invoice) and row.kind != "invoice":
        raise Conflict("sales.invalid_transition", detail="Credit notes cannot be marked paid")
    if action in ("send", "confirm", "issue"):
        if not repo.lines(db, ctx, family, row.id):
            raise Invalid("sales.lines_required")
        issue_date = clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()
        prefix = {"quote": "QUO", "order": "SO", "invoice": "INV"}[family]
        number = repo.next_number(db, ctx, prefix, issue_date.year)
        row.issue_date, row.number = issue_date, number
    # Queries in stock transitions may autoflush: status and number must already form a valid pair.
    row.status = flow[1]
    if family == "order":
        stock_transition(db, ctx, row, action)
    if action == "mark-paid":
        assert isinstance(row, Invoice)
        row.paid_at = clock.now()
    row.version += 1
    record(db, ctx, family, row, action)
    return row


def stock_transition(db: Session, ctx: Context, row: Document, action: str) -> None:
    if action == "confirm":
        lines = repo.lines(db, ctx, "order", row.id)
        items = catalog.get_items(db, ctx, {line.catalog_item_id for line in lines if line.catalog_item_id})
        # Lock stock in a consistent item order, including documents with repeated item lines.
        quantities: dict[uuid.UUID, Decimal] = {}
        for line in lines:
            if line.catalog_item_id and items[line.catalog_item_id].is_stock_tracked:
                quantities[line.catalog_item_id] = quantities.get(line.catalog_item_id, Decimal("0")) + line.quantity
        for item_id in sorted(quantities):
            inventory.reserve_stock(
                db, ctx, item_id=item_id, quantity=quantities[item_id], ref_type="sales_order", ref_id=row.id
            )
    elif action == "fulfil":
        inventory.consume_reservations(db, ctx, ref_type="sales_order", ref_id=row.id, reason="sale")
    elif action == "cancel":
        inventory.release_reservations(db, ctx, ref_type="sales_order", ref_id=row.id)


def copy_document(
    db: Session, ctx: Context, source_family: DocumentType, source: Document, target_family: DocumentType, **links: Any
) -> Document:
    fields = ("customer_id", "bill_to_name", "bill_to_email", "bill_to_address", "currency", "due_date", "notes")
    row = repo.MODELS[target_family](
        business_id=ctx.business_id, **{field: getattr(source, field) for field in fields}, **links
    )
    db.add(row)
    db.flush()
    values = [
        {
            field: getattr(line, field)
            for field in (
                "catalog_item_id",
                "name",
                "quantity",
                "unit_price_minor",
                "tax_rate",
                "line_subtotal_minor",
                "line_tax_minor",
                "line_total_minor",
                "position",
            )
        }
        for line in repo.lines(db, ctx, source_family, source.id)
    ]
    store_lines(db, ctx, target_family, row, values)
    record(db, ctx, target_family, row, "created")
    return row


def convert_quote(db: Session, ctx: Context, row_id: uuid.UUID, version: int) -> Document:
    ctx.require(P.DOCUMENT_WRITE)
    quote = repo.get(db, ctx, "quote", row_id, lock=True)
    check_version(quote, version)
    require_status(quote, "accepted")
    if repo.converted(db, ctx, "order", "source_quote_id", row_id):
        raise Conflict("sales.already_converted")
    row = copy_document(db, ctx, "quote", quote, "order", source_quote_id=row_id)
    quote.version += 1
    record(db, ctx, "quote", quote, "converted")
    return row


def invoice_order(db: Session, ctx: Context, row_id: uuid.UUID, version: int) -> Document:
    ctx.require(P.DOCUMENT_WRITE)
    order = repo.get(db, ctx, "order", row_id, lock=True)
    check_version(order, version)
    if order.status not in ("confirmed", "fulfilled"):
        raise Conflict("sales.invalid_transition", detail="Invoice requires a confirmed or fulfilled order")
    if repo.converted(db, ctx, "invoice", "source_order_id", row_id):
        raise Conflict("sales.already_converted")
    assert isinstance(order, SalesOrder)
    row = copy_document(
        db, ctx, "order", order, "invoice", source_order_id=row_id, source_quote_id=order.source_quote_id
    )
    order.version += 1
    record(db, ctx, "order", order, "invoiced")
    return row


def credit(db: Session, ctx: Context, row_id: uuid.UUID, version: int) -> Document:
    ctx.require(P.CREDIT)
    original = repo.get(db, ctx, "invoice", row_id, lock=True)
    check_version(original, version)
    require_status(original, "issued")
    assert isinstance(original, Invoice)
    if original.kind != "invoice":
        raise Conflict("sales.invalid_transition", detail="Cannot credit a credit note")
    issue_date = clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()
    number = repo.next_number(db, ctx, "CN", issue_date.year)
    row = copy_document(
        db,
        ctx,
        "invoice",
        original,
        "invoice",
        kind="credit_note",
        credit_of_invoice_id=row_id,
        status="issued",
        number=number,
        issue_date=issue_date,
    )
    original.status = "void"
    original.version += 1
    record(db, ctx, "invoice", original, "credited")
    record(db, ctx, "invoice", row, "issued")
    return row
