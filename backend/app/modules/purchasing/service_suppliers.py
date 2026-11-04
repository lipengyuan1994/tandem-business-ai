from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.platform import audit
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid

from . import permissions as P
from . import repository as repo
from .models import Supplier, SupplierItem
from .schemas import SupplierIn, SupplierItemIn, SupplierOut, SupplierUpdate


def detail(db: Session, ctx: Context, row: Supplier) -> SupplierOut:
    return SupplierOut(
        id=row.id,
        name=row.name,
        email=row.email,
        phone=row.phone,
        notes=row.notes,
        version=row.version,
        created_at=row.created_at,
        items=[SupplierItemIn.model_validate(i, from_attributes=True) for i in repo.supplier_items(db, ctx, row.id)],
    )


def save_items(db: Session, ctx: Context, row: Supplier, items: list[SupplierItemIn]) -> None:
    if len({i.catalog_item_id for i in items}) != len(items):
        raise Invalid("purchasing.duplicate_supplier_item")
    products = catalog.get_items(db, ctx, {i.catalog_item_id for i in items})
    for item in items:
        product = products.get(item.catalog_item_id)
        if product is None or product.kind != "product" or not product.is_stock_tracked or product.status != "active":
            raise Invalid("purchasing.invalid_item", detail="Choose an active stock product")
    repo.delete_supplier_items(db, ctx, row.id)
    db.add_all(SupplierItem(business_id=ctx.business_id, supplier_id=row.id, **i.model_dump()) for i in items)


def record(db: Session, ctx: Context, row: Supplier, action: str) -> None:
    db.flush()
    audit.record(
        db,
        ctx,
        action=f"purchasing.supplier.{action}",
        target_type="purchasing.supplier",
        target_id=row.id,
        summary=f"Supplier {action}",
        changes={"version": row.version},
    )


def create(db: Session, ctx: Context, data: SupplierIn) -> Supplier:
    ctx.require(P.WRITE)
    row = Supplier(business_id=ctx.business_id, **data.model_dump(exclude={"items"}))
    db.add(row)
    db.flush()
    save_items(db, ctx, row, data.items)
    record(db, ctx, row, "created")
    return row


def update(db: Session, ctx: Context, row_id: uuid.UUID, data: SupplierUpdate) -> Supplier:
    ctx.require(P.WRITE)
    row = repo.supplier(db, ctx, row_id, lock=True)
    if row.version != data.version:
        raise Conflict("purchasing.version_conflict", current_version=row.version)
    for key, value in data.model_dump(exclude={"version", "items"}, exclude_unset=True).items():
        setattr(row, key, value)
    if data.items is not None:
        save_items(db, ctx, row, data.items)
    row.version += 1
    record(db, ctx, row, "updated")
    return row
