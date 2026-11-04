"""Business-scoped reads and locks; no cross-module SQL."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.platform import crud
from app.platform.context import Context
from app.platform.ids import new_id

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderSequence,
    Supplier,
    SupplierItem,
)


def supplier(db: Session, ctx: Context, row_id: uuid.UUID, *, lock: bool = False) -> Supplier:
    return crud.require_scoped(db, ctx, Supplier, row_id, "purchasing.supplier_not_found", for_update=lock)


def order(db: Session, ctx: Context, row_id: uuid.UUID, *, lock: bool = False) -> PurchaseOrder:
    return crud.require_scoped(db, ctx, PurchaseOrder, row_id, "purchasing.po_not_found", for_update=lock)


def suppliers(db: Session, ctx: Context, limit: int, cursor: str | None) -> tuple[list[Supplier], str | None]:
    return crud.page(db, select(Supplier).where(Supplier.business_id == ctx.business_id), Supplier, limit, cursor)


def orders(
    db: Session, ctx: Context, limit: int, cursor: str | None, status: str | None
) -> tuple[list[PurchaseOrder], str | None]:
    stmt = select(PurchaseOrder).where(PurchaseOrder.business_id == ctx.business_id)
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    return crud.page(db, stmt, PurchaseOrder, limit, cursor)


def supplier_items(db: Session, ctx: Context, row_id: uuid.UUID) -> list[SupplierItem]:
    return list(
        db.scalars(
            select(SupplierItem)
            .where(SupplierItem.business_id == ctx.business_id, SupplierItem.supplier_id == row_id)
            .order_by(SupplierItem.id)
        )
    )


def delete_supplier_items(db: Session, ctx: Context, row_id: uuid.UUID) -> None:
    db.execute(
        delete(SupplierItem).where(SupplierItem.business_id == ctx.business_id, SupplierItem.supplier_id == row_id)
    )


def lines(db: Session, ctx: Context, row_id: uuid.UUID) -> list[PurchaseOrderLine]:
    return list(
        db.scalars(
            select(PurchaseOrderLine)
            .where(PurchaseOrderLine.business_id == ctx.business_id, PurchaseOrderLine.purchase_order_id == row_id)
            .order_by(PurchaseOrderLine.position)
        )
    )


def delete_lines(db: Session, ctx: Context, row_id: uuid.UUID) -> None:
    db.execute(
        delete(PurchaseOrderLine).where(
            PurchaseOrderLine.business_id == ctx.business_id, PurchaseOrderLine.purchase_order_id == row_id
        )
    )


def receipts(db: Session, ctx: Context, row_id: uuid.UUID) -> list[GoodsReceipt]:
    return list(
        db.scalars(
            select(GoodsReceipt)
            .where(GoodsReceipt.business_id == ctx.business_id, GoodsReceipt.purchase_order_id == row_id)
            .order_by(GoodsReceipt.id)
        )
    )


def receipt_lines(db: Session, ctx: Context, row_id: uuid.UUID) -> list[GoodsReceiptLine]:
    return list(
        db.scalars(
            select(GoodsReceiptLine)
            .where(GoodsReceiptLine.business_id == ctx.business_id, GoodsReceiptLine.receipt_id == row_id)
            .order_by(GoodsReceiptLine.id)
        )
    )


def open_quantities(db: Session, ctx: Context) -> dict[uuid.UUID, Decimal]:
    rows = db.execute(
        select(
            PurchaseOrderLine.catalog_item_id,
            func.sum(func.greatest(PurchaseOrderLine.quantity - PurchaseOrderLine.received_quantity, 0)),
        )
        .join(
            PurchaseOrder,
            (PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            & (PurchaseOrder.business_id == PurchaseOrderLine.business_id),
        )
        .where(
            PurchaseOrder.business_id == ctx.business_id,
            PurchaseOrder.status.in_(("approved", "sent", "partially_received")),
        )
        .group_by(PurchaseOrderLine.catalog_item_id)
    )
    return {item_id: Decimal(quantity) for item_id, quantity in rows}


def item_suppliers(db: Session, ctx: Context, item_id: uuid.UUID) -> list[Supplier]:
    return list(
        db.scalars(
            select(Supplier)
            .join(
                SupplierItem,
                (SupplierItem.supplier_id == Supplier.id) & (SupplierItem.business_id == Supplier.business_id),
            )
            .where(Supplier.business_id == ctx.business_id, SupplierItem.catalog_item_id == item_id)
            .order_by(Supplier.name)
        )
    )


def next_number(db: Session, ctx: Context, year: int) -> str:
    db.execute(
        insert(PurchaseOrderSequence)
        .values(id=new_id(), business_id=ctx.business_id, year=year, next_value=1)
        .on_conflict_do_nothing(index_elements=["business_id", "year"])
    )
    sequence = db.scalar(
        select(PurchaseOrderSequence)
        .where(PurchaseOrderSequence.business_id == ctx.business_id, PurchaseOrderSequence.year == year)
        .with_for_update()
    )
    assert sequence is not None
    value = sequence.next_value
    sequence.next_value += 1
    sequence.version += 1
    db.flush()
    return f"PO-{year}-{value:04d}"


def lock_idempotency(db: Session, ctx: Context, key: str) -> None:
    """Serialize first use of a key as well as replays. The lock lasts until commit or rollback."""
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"purchasing:{ctx.business_id}:{key}"}
    )
