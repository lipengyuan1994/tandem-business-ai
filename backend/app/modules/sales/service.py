"""Shared staff/executor document operations. The caller owns the transaction; services never commit."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.platform import audit, clock, events
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid

from . import payments
from . import permissions as P
from . import repository as repo
from .calculations import line_values, prepare_lines
from .events import DocumentChanged
from .models import Document, Invoice, InvoiceLine, InvoiceRefund, SalesOrder
from .schemas import (
    CreditIn,
    CreditLineIn,
    CreditNoteRef,
    DocumentOut,
    DocumentType,
    DocumentUpdate,
    LineOut,
    PaymentIn,
    PaymentOut,
    RefundOut,
)

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


def record(db: Session, ctx: Context, family: DocumentType, row: Document, action: str, **changes: object) -> None:
    db.flush()
    audit.record(
        db,
        ctx,
        action=f"sales.{family}.{action}",
        target_type=f"sales.{family}",
        target_id=row.id,
        summary=f"Sales {family}: {action}",
        changes={"status": row.status, "version": row.version, **changes},
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
    if isinstance(row, Invoice):
        if row.kind == "invoice":
            out.balance_minor = payments.balance(row) if row.status in ("issued", "paid") else None
            out.payment_status = payments.payment_status(row)
            out.payments = [PaymentOut.model_validate(p) for p in payments.for_invoice(db, ctx, row.id)]
            if row.status != "draft":
                done = credited_so_far(db, ctx, row.id)
                for line in out.lines:
                    qty, sub, tax = done.get(line.id, (Decimal("0.000"), 0, 0))
                    line.credited_quantity, line.credited_subtotal_minor, line.credited_tax_minor = qty, sub, tax
                out.credit_notes = [CreditNoteRef.model_validate(n) for n in credit_notes_of(db, ctx, row.id)]
                out.refunds = [RefundOut.model_validate(r) for r in refunds_of(db, ctx, row.id)]
        else:
            out.amount_paid_minor = None
            out.amount_credited_minor = None
            out.amount_refunded_minor = None
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
    if family == "order" and action == "cancel" and repo.has_live_invoice(db, ctx, row.id):
        # invoice_order locks the same order row, so this check cannot race with invoicing it
        raise Conflict("sales.order_invoiced", detail="The order has an invoice; credit or delete the invoice instead")
    if action == "mark-paid":
        assert isinstance(row, Invoice)
        if row.kind != "invoice":
            raise Conflict("sales.invalid_transition", detail="Credit notes cannot be marked paid")
        # Marking paid records the open balance as one payment received today, so history and aging stay exact.
        amount = payments.balance(row)
        payments.add(db, ctx, row, amount, business_today(db, ctx), "other", None)
        row.version += 1
        record(db, ctx, family, row, action, amount_minor=amount)
        return row
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
    row.version += 1
    record(db, ctx, family, row, action)
    return row


def business_today(db: Session, ctx: Context) -> date:
    return clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()


def record_payment(db: Session, ctx: Context, row_id: uuid.UUID, data: PaymentIn) -> Invoice:
    ctx.require(P.MARK_PAID)
    row = repo.get(db, ctx, "invoice", row_id, lock=True)
    assert isinstance(row, Invoice)
    check_version(row, data.version)
    if data.paid_on > business_today(db, ctx):
        raise Invalid("sales.payment_date_in_future")
    payment = payments.add(db, ctx, row, data.amount_minor, data.paid_on, data.method, data.reference)
    assert payment is not None
    row.version += 1
    record(
        db,
        ctx,
        "invoice",
        row,
        "payment_recorded",
        payment_id=str(payment.id),
        amount_minor=payment.amount_minor,
        method=payment.method,
        paid_on=payment.paid_on.isoformat(),
    )
    return row


def void_payment(db: Session, ctx: Context, row_id: uuid.UUID, payment_id: uuid.UUID, version: int) -> Invoice:
    ctx.require(P.CREDIT)
    row = repo.get(db, ctx, "invoice", row_id, lock=True)
    assert isinstance(row, Invoice)
    check_version(row, version)
    payment = payments.void(db, ctx, row, payment_id)
    row.version += 1
    record(db, ctx, "invoice", row, "payment_voided", payment_id=str(payment.id), amount_minor=payment.amount_minor)
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


def credited_so_far(db: Session, ctx: Context, invoice_id: uuid.UUID) -> dict[uuid.UUID, tuple[Decimal, int, int]]:
    """Per invoice line: quantity, subtotal and tax its credit notes have credited so far."""
    rows = db.execute(
        select(
            InvoiceLine.credited_line_id,
            func.sum(InvoiceLine.quantity),
            func.sum(InvoiceLine.line_subtotal_minor),
            func.sum(InvoiceLine.line_tax_minor),
        )
        .join(
            Invoice,
            (Invoice.business_id == InvoiceLine.business_id) & (Invoice.id == InvoiceLine.document_id),
        )
        .where(
            InvoiceLine.business_id == ctx.business_id,
            Invoice.credit_of_invoice_id == invoice_id,
            Invoice.kind == "credit_note",
            InvoiceLine.credited_line_id.is_not(None),
        )
        .group_by(InvoiceLine.credited_line_id)
    ).all()
    return {line_id: (Decimal(qty), int(sub), int(tax)) for line_id, qty, sub, tax in rows if line_id is not None}


def _credit_lines(
    db: Session, ctx: Context, original: Invoice, wanted: list[CreditLineIn] | None
) -> list[dict[str, Any]]:
    """Credit note lines for the wanted quantities. Crediting what is left of a line takes exactly what is left of its
    amounts, so a line credited in parts never adds up to more or less than the invoice charged."""
    lines = {line.id: line for line in repo.lines(db, ctx, "invoice", original.id)}
    done = credited_so_far(db, ctx, original.id)
    if wanted is None:
        quantities = {
            line_id: line.quantity - done.get(line_id, (Decimal(0), 0, 0))[0]
            for line_id, line in lines.items()
            if line.quantity > done.get(line_id, (Decimal(0), 0, 0))[0]
        }
        if not quantities:
            raise Conflict("sales.nothing_to_credit", detail="Everything on this invoice is already credited")
    else:
        quantities = {}
        for item in wanted:
            if item.line_id not in lines:
                raise Invalid("sales.credit_line_unknown", detail="A line is not on this invoice")
            if item.line_id in quantities:
                raise Invalid("sales.credit_line_repeated", detail="Each invoice line can be listed once")
            quantities[item.line_id] = item.quantity
    values: list[dict[str, Any]] = []
    for line in sorted(lines.values(), key=lambda x: x.position):
        if line.id not in quantities:
            continue
        quantity = quantities[line.id]
        qty_done, sub_done, tax_done = done.get(line.id, (Decimal(0), 0, 0))
        left = line.quantity - qty_done
        if quantity > left:
            raise Conflict(
                "sales.credit_exceeds_line",
                detail=f"Only {left.normalize():f} of '{line.name}' is left to credit",
                line_id=str(line.id),
            )
        if quantity == left:
            subtotal = line.line_subtotal_minor - sub_done
            tax = line.line_tax_minor - tax_done
        else:
            subtotal, tax, _ = line_values(quantity, line.unit_price_minor, line.tax_rate)
            # Never let a partial credit take more than is left of the line's amounts after rounding.
            subtotal, tax = min(subtotal, line.line_subtotal_minor - sub_done), min(tax, line.line_tax_minor - tax_done)
        values.append(
            {
                "catalog_item_id": line.catalog_item_id,
                "name": line.name,
                "quantity": quantity,
                "unit_price_minor": line.unit_price_minor,
                "tax_rate": line.tax_rate,
                "line_subtotal_minor": subtotal,
                "line_tax_minor": tax,
                "line_total_minor": subtotal + tax,
                "position": len(values),
                "credited_line_id": line.id,
            }
        )
    return values


def credit(db: Session, ctx: Context, row_id: uuid.UUID, version: int, data: CreditIn | None = None) -> Document:
    """Issue a credit note for some or all of an issued or paid invoice.

    The credit first reduces what the customer still owes. Whatever it exceeds that by was already paid, so it is paid
    back as a refund, recorded with the credit note. Credited in full, the invoice becomes void.
    """
    ctx.require(P.CREDIT)
    data = data or CreditIn(version=version)
    original = repo.get(db, ctx, "invoice", row_id, lock=True)
    check_version(original, version)
    assert isinstance(original, Invoice)
    if original.kind != "invoice":
        raise Conflict("sales.invalid_transition", detail="Cannot credit a credit note")
    if original.status not in ("issued", "paid"):
        raise Conflict("sales.invalid_transition", detail=f"Cannot credit a {original.status} invoice")
    values = _credit_lines(db, ctx, original, data.lines)
    amount = sum(v["line_total_minor"] for v in values)
    if amount == 0:
        raise Conflict("sales.nothing_to_credit", detail="The credit would be for nothing")
    refund_minor = max(0, amount - payments.balance(original))
    today = business_today(db, ctx)
    if refund_minor and data.refund is None:
        raise Conflict(
            "sales.refund_required",
            detail="The credit is more than the customer still owes; say how the difference is paid back",
            refund_minor=refund_minor,
        )
    if not refund_minor and data.refund is not None:
        raise Invalid("sales.refund_not_needed", detail="The customer still owes more than this credit")
    if data.refund is not None and data.refund.paid_on > today:
        raise Invalid("sales.payment_date_in_future")
    if data.refund is not None and not payments.refund_dates_covered(
        db, ctx, row_id, extra=(data.refund.paid_on, refund_minor)
    ):
        raise Conflict(
            "sales.refund_exceeds_dated_payments",
            detail="Recorded payments do not cover all refunds on their effective dates",
        )
    number = repo.next_number(db, ctx, "CN", today.year)
    fields = ("customer_id", "bill_to_name", "bill_to_email", "bill_to_address", "currency", "due_date")
    note = Invoice(
        business_id=ctx.business_id,
        **{field: getattr(original, field) for field in fields},
        notes=data.notes if data.notes is not None else original.notes,
        kind="credit_note",
        credit_of_invoice_id=row_id,
        status="issued",
        number=number,
        issue_date=today,
    )
    db.add(note)
    db.flush()
    store_lines(db, ctx, "invoice", note, values)
    refund = None
    if refund_minor:
        assert data.refund is not None
        refund = InvoiceRefund(
            business_id=ctx.business_id,
            invoice_id=original.id,
            credit_note_id=note.id,
            amount_minor=refund_minor,
            paid_on=data.refund.paid_on,
            method=data.refund.method,
            reference=data.refund.reference,
        )
        db.add(refund)
    original.amount_credited_minor += amount
    original.amount_refunded_minor += refund_minor
    payments.settle_after_credit(original)
    original.version += 1
    db.flush()
    record(
        db,
        ctx,
        "invoice",
        original,
        "credited",
        credit_note_id=str(note.id),
        amount_minor=amount,
        refund_minor=refund_minor,
    )
    record(db, ctx, "invoice", note, "issued")
    return note


def refunds_of(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[InvoiceRefund]:
    return list(
        db.scalars(
            select(InvoiceRefund)
            .where(InvoiceRefund.business_id == ctx.business_id, InvoiceRefund.invoice_id == invoice_id)
            .order_by(InvoiceRefund.paid_on, InvoiceRefund.created_at, InvoiceRefund.id)
        )
    )


def credit_notes_of(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[Invoice]:
    return list(
        db.scalars(
            select(Invoice)
            .where(
                Invoice.business_id == ctx.business_id,
                Invoice.credit_of_invoice_id == invoice_id,
                Invoice.kind == "credit_note",
            )
            .order_by(Invoice.issue_date, Invoice.number)
        )
    )
