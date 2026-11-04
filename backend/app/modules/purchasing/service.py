"""Purchase document rules shared by staff endpoints and approval executors."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid

from . import permissions as P
from . import repository as repo
from . import service_suppliers as suppliers
from .models import PurchaseOrder, PurchaseOrderLine
from .schemas import (
    ConversionSnapshot,
    POCreate,
    POLineIn,
    POLineOut,
    POOut,
    POUpdate,
    PricedLine,
    PurchasingSuggestion,
    ReceiptLineIn,
    ReceiptOut,
)


def check_version(row: PurchaseOrder, version: int) -> None:
    if row.version != version:
        raise Conflict("purchasing.version_conflict", current_version=row.version)


def record(db: Session, ctx: Context, row: PurchaseOrder, action: str) -> None:
    db.flush()
    audit.record(
        db,
        ctx,
        action=f"purchasing.po.{action}",
        target_type="purchasing.po",
        target_id=row.id,
        summary=f"Purchase order {action}",
        changes={"status": row.status, "version": row.version},
    )


def prepare_lines(db: Session, ctx: Context, lines: list[POLineIn]) -> list[PricedLine]:
    products = catalog.get_items(db, ctx, {line.catalog_item_id for line in lines})
    values = []
    for position, line in enumerate(lines):
        item = products.get(line.catalog_item_id)
        if item is None or item.kind != "product" or not item.is_stock_tracked or item.status != "active":
            raise Invalid("purchasing.invalid_item", detail="Choose an active stock product")
        if item.unit == "each" and line.quantity != line.quantity.to_integral_value():
            raise Invalid("inventory.fractional_quantity")
        total = int((line.quantity * line.unit_cost_minor).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if total > 10**15:
            raise Invalid("purchasing.amount_too_large")
        values.append(PricedLine(**line.model_dump(), name=item.name, position=position, line_total_minor=total))
    if sum(line.line_total_minor for line in values) > 10**15:
        raise Invalid("purchasing.amount_too_large")
    return values


def store_lines(db: Session, ctx: Context, row: PurchaseOrder, lines: list[PricedLine]) -> None:
    db.add_all(
        PurchaseOrderLine(business_id=ctx.business_id, purchase_order_id=row.id, **line.model_dump()) for line in lines
    )
    row.total_minor = sum(line.line_total_minor for line in lines)
    db.flush()


def create(db: Session, ctx: Context, data: POCreate, *, expected: ConversionSnapshot | None = None) -> PurchaseOrder:
    ctx.require(P.WRITE)
    supplier = repo.supplier(db, ctx, data.supplier_id)
    values = prepare_lines(db, ctx, data.lines)
    currency = catalog.get_default_currency(db, ctx)
    actual = ConversionSnapshot(
        supplier_name=supplier.name,
        currency=currency,
        notes=data.notes,
        lines=values,
        total_minor=sum(line.line_total_minor for line in values),
    )
    if expected is not None and actual != expected:
        raise Conflict("purchasing.preview_changed")
    row = PurchaseOrder(
        business_id=ctx.business_id,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        currency=currency,
        notes=data.notes,
    )
    db.add(row)
    db.flush()
    store_lines(db, ctx, row, values)
    record(db, ctx, row, "created")
    return row


def detail(db: Session, ctx: Context, row: PurchaseOrder) -> POOut:
    out = POOut.model_validate(row)
    out.lines = [POLineOut.model_validate(line) for line in repo.lines(db, ctx, row.id)]
    for receipt in repo.receipts(db, ctx, row.id):
        value = ReceiptOut.model_validate(receipt)
        value.lines = [
            ReceiptLineIn.model_validate(line, from_attributes=True) for line in repo.receipt_lines(db, ctx, receipt.id)
        ]
        out.receipts.append(value)
    return out


def update(db: Session, ctx: Context, row_id: uuid.UUID, data: POUpdate) -> PurchaseOrder:
    ctx.require(P.WRITE)
    row = repo.order(db, ctx, row_id, lock=True)
    check_version(row, data.version)
    if row.status != "draft" and (
        row.status == "received" or data.model_fields_set - {"version", "expected_delivery_date"}
    ):
        raise Conflict("purchasing.po_locked")
    if "expected_delivery_date" in data.model_fields_set:
        row.expected_delivery_date = data.expected_delivery_date
    if data.lines is not None:
        values = prepare_lines(db, ctx, data.lines)
        repo.delete_lines(db, ctx, row.id)
        store_lines(db, ctx, row, values)
    if "notes" in data.model_fields_set:
        row.notes = data.notes
    row.version += 1
    record(db, ctx, row, "updated")
    return row


def transition(db: Session, ctx: Context, row_id: uuid.UUID, version: int, action: str) -> PurchaseOrder:
    ctx.require(P.APPROVE if action == "approve" else P.WRITE)
    row = repo.order(db, ctx, row_id, lock=True)
    check_version(row, version)
    allowed = {
        "approve": ("draft",),
        "send": ("approved",),
        "cancel": ("draft", "approved", "sent", "partially_received"),
    }
    if row.status not in allowed.get(action, ()):
        raise Conflict("purchasing.invalid_transition")
    if action == "approve":
        today = clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()
        row.number = repo.next_number(db, ctx, today.year)
        row.approved_date = today
    if action == "send":
        row.sent_at = clock.now()
        today = row.sent_at.astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()
        item_ids = {line.catalog_item_id for line in repo.lines(db, ctx, row.id)}
        supplier_items = {i.catalog_item_id: i.lead_time_days for i in repo.supplier_items(db, ctx, row.supplier_id)}
        lead = max((supplier_items.get(item_id, 0) for item_id in item_ids), default=0)
        if row.source_quote_id is not None:
            from .quote_repository import quote

            lead = quote(db, ctx, row.source_quote_id).lead_time_days
        row.expected_delivery_date = today + timedelta(days=lead)
    row.status = {"approve": "approved", "send": "sent", "cancel": "cancelled"}[action]
    row.version += 1
    record(db, ctx, row, action)
    return row


def suggestions(db: Session, ctx: Context) -> list[PurchasingSuggestion]:
    quantities = repo.open_quantities(db, ctx)
    return [
        PurchasingSuggestion(
            item_id=s.item_id,
            item_name=s.item_name,
            available=s.available,
            reorder_point=s.reorder_point,
            target_level=s.target_level,
            open_po_quantity=quantities.get(s.item_id, Decimal(0)),
            suggested=max(Decimal(0), s.suggested - quantities.get(s.item_id, Decimal(0))),
            suppliers=[suppliers.detail(db, ctx, row) for row in repo.item_suppliers(db, ctx, s.item_id)],
        )
        for s in inventory.list_below_reorder(db, ctx)
    ]
