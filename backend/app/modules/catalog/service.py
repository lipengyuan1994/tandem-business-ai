"""Catalog rules (M05): services need duration + resource kind; parts must be stock-tracked active products."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform import audit, crud
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid, NotFound
from app.platform.ids import new_id

from . import permissions as P
from .models import CatalogItem, ServicePart
from .schemas import CatalogItemCreate, CatalogItemUpdate, ServicePartIn, ServicePartOut, ServiceRequirements

FIELDS = [
    "sku",
    "name",
    "description",
    "price_minor",
    "duration_minutes",
    "buffer_minutes",
    "is_bookable_online",
    "recall_interval_months",
]


def _item(db: Session, ctx: Context, item_id: uuid.UUID, *, for_update: bool = False) -> CatalogItem:
    return crud.require_scoped(db, ctx, CatalogItem, item_id, "catalog.item_not_found", for_update=for_update)


def _sku_free(db: Session, ctx: Context, sku: str | None, except_id: uuid.UUID | None = None) -> None:
    if not sku:
        return
    stmt = select(CatalogItem.id).where(CatalogItem.business_id == ctx.business_id, CatalogItem.sku == sku)
    if except_id:
        stmt = stmt.where(CatalogItem.id != except_id)
    if db.scalar(stmt) is not None:
        raise Conflict("catalog.sku_taken")


def search_items(
    db: Session,
    ctx: Context,
    *,
    query: str | None,
    kind: str | None,
    include_archived: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[CatalogItem], str | None]:
    ctx.require(P.ITEM_READ)
    stmt = select(CatalogItem).where(CatalogItem.business_id == ctx.business_id)
    if kind:
        stmt = stmt.where(CatalogItem.kind == kind)
    if not include_archived:
        stmt = stmt.where(CatalogItem.status == "active")
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(or_(CatalogItem.name.ilike(like), CatalogItem.sku.ilike(like)))
    return crud.page(db, stmt, CatalogItem, limit, cursor)


def get_item(db: Session, ctx: Context, item_id: uuid.UUID) -> CatalogItem:
    ctx.require(P.ITEM_READ)
    return _item(db, ctx, item_id)


def create_item(db: Session, ctx: Context, data: CatalogItemCreate) -> CatalogItem:
    ctx.require(P.ITEM_WRITE)
    _sku_free(db, ctx, data.sku)
    assert ctx.business_id is not None
    item = CatalogItem(
        id=new_id(),
        business_id=ctx.business_id,
        currency=org.get_business_brief(db, ctx).currency,
        status="active",
        **data.model_dump(),
    )
    db.add(item)
    db.flush()
    audit.record(
        db,
        ctx,
        action="catalog.item.created",
        target_type="catalog_item",
        target_id=item.id,
        summary=f"{data.kind.title()} '{data.name}' created",
    )
    return item


def update_item(db: Session, ctx: Context, item_id: uuid.UUID, data: CatalogItemUpdate) -> CatalogItem:
    ctx.require(P.ITEM_WRITE)
    before = _item(db, ctx, item_id)
    if before.kind == "product" and data.recall_interval_months is not None:
        raise Invalid("catalog.product_has_no_recall")
    snapshot = {f: getattr(before, f) for f in FIELDS}
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    if "sku" in changes:
        _sku_free(db, ctx, changes["sku"], except_id=item_id)
    if before.kind == "product" and changes.get("duration_minutes") is not None:
        raise Invalid("catalog.product_has_no_duration")
    if before.kind == "service" and "duration_minutes" in changes and changes["duration_minutes"] is None:
        raise Invalid("catalog.service_requires_duration")
    after = crud.update_versioned(db, ctx, CatalogItem, item_id, data.version, changes, "catalog.item_version_conflict")
    audit.record(
        db,
        ctx,
        action="catalog.item.updated",
        target_type="catalog_item",
        target_id=item_id,
        summary=f"'{after.name}' updated",
        changes=audit.diff(snapshot, after, FIELDS),
    )
    return after


def archive_item(db: Session, ctx: Context, item_id: uuid.UUID, version: int) -> CatalogItem:
    ctx.require(P.ITEM_WRITE)
    item = _item(db, ctx, item_id)
    if item.kind == "product":
        used_by = db.scalar(
            select(CatalogItem.name)
            .join(ServicePart, ServicePart.service_id == CatalogItem.id)
            .where(
                ServicePart.item_id == item_id,
                CatalogItem.status == "active",
                CatalogItem.business_id == ctx.business_id,
            )
        )
        if used_by:
            raise Conflict("catalog.item_in_use", detail=f"required by service '{used_by}'")
    after = crud.update_versioned(
        db, ctx, CatalogItem, item_id, version, {"status": "archived"}, "catalog.item_version_conflict"
    )
    audit.record(
        db,
        ctx,
        action="catalog.item.archived",
        target_type="catalog_item",
        target_id=item_id,
        summary=f"'{after.name}' archived",
    )
    return after


def get_parts(db: Session, ctx: Context, service_id: uuid.UUID) -> list[ServicePartOut]:
    rows = db.execute(
        select(ServicePart.item_id, CatalogItem.name, ServicePart.quantity)
        .join(CatalogItem, CatalogItem.id == ServicePart.item_id)
        .where(ServicePart.business_id == ctx.business_id, ServicePart.service_id == service_id)
        .order_by(CatalogItem.name)
    )
    return [ServicePartOut(item_id=i, item_name=n, quantity=q) for i, n, q in rows]


def replace_parts(db: Session, ctx: Context, service_id: uuid.UUID, parts: list[ServicePartIn]) -> list[ServicePartOut]:
    ctx.require(P.ITEM_WRITE)
    service = _item(db, ctx, service_id, for_update=True)
    if service.kind != "service":
        raise Invalid("catalog.invalid_part", detail="parts belong to services")
    if len({p.item_id for p in parts}) != len(parts):
        raise Invalid("catalog.invalid_part", detail="duplicate item")
    for p in parts:
        item = crud.get_scoped(db, ctx, CatalogItem, p.item_id)
        if item is None or item.kind != "product" or not item.is_stock_tracked or item.status != "active":
            raise Invalid("catalog.invalid_part", detail="a part must be an active, stock-tracked product")
    db.execute(
        delete(ServicePart).where(ServicePart.business_id == ctx.business_id, ServicePart.service_id == service_id)
    )
    assert ctx.business_id is not None
    db.add_all(
        ServicePart(business_id=ctx.business_id, service_id=service_id, item_id=p.item_id, quantity=p.quantity)
        for p in parts
    )
    db.flush()
    audit.record(
        db,
        ctx,
        action="catalog.service_parts.replaced",
        target_type="catalog_item",
        target_id=service_id,
        summary=f"Parts for '{service.name}' set ({len(parts)})",
    )
    return get_parts(db, ctx, service_id)


def requirements(db: Session, ctx: Context, service_id: uuid.UUID) -> ServiceRequirements:
    """Internal read for other modules (scheduling): no permission check of its own."""
    s = _item(db, ctx, service_id)
    if s.kind != "service" or s.status != "active" or s.duration_minutes is None or s.resource_kind is None:
        raise NotFound("catalog.service_not_found")
    return ServiceRequirements(
        service_id=s.id,
        name=s.name,
        duration_minutes=s.duration_minutes,
        buffer_minutes=s.buffer_minutes,
        resource_kind=s.resource_kind,
        price_minor=s.price_minor,
        currency=s.currency,
        parts=get_parts(db, ctx, s.id),
    )


def items_by_id(db: Session, ctx: Context, ids: set[uuid.UUID]) -> dict[uuid.UUID, CatalogItem]:
    if not ids:
        return {}
    rows = db.scalars(select(CatalogItem).where(CatalogItem.business_id == ctx.business_id, CatalogItem.id.in_(ids)))
    return {r.id: r for r in rows}


def public_services(db: Session, ctx: Context) -> list[CatalogItem]:
    stmt = (
        select(CatalogItem)
        .where(
            CatalogItem.business_id == ctx.business_id,
            CatalogItem.kind == "service",
            CatalogItem.status == "active",
            CatalogItem.is_bookable_online.is_(True),
        )
        .order_by(CatalogItem.name)
    )
    return list(db.scalars(stmt))
