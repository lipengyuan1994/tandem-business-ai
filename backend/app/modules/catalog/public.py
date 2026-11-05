"""Public API of the catalog module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.platform.context import Context

from . import service
from .schemas import CatalogItemOut, PublicServiceOut, ServicePartOut, ServiceRequirements


def get_item(db: Session, ctx: Context, item_id: uuid.UUID) -> CatalogItemOut:
    return CatalogItemOut.model_validate(service.get_item(db, ctx, item_id))


def get_items(db: Session, ctx: Context, item_ids: set[uuid.UUID]) -> dict[uuid.UUID, CatalogItemOut]:
    """Internal batch read (names, units) for other modules; no permission check of its own."""
    return {k: CatalogItemOut.model_validate(v) for k, v in service.items_by_id(db, ctx, item_ids).items()}


def list_stock_tracked(db: Session, ctx: Context) -> list[CatalogItemOut]:
    """Internal read for inventory: every active stock-tracked product (no permission check of its own)."""
    from sqlalchemy import select

    from .models import CatalogItem

    rows = db.scalars(
        select(CatalogItem)
        .where(
            CatalogItem.business_id == ctx.business_id,
            CatalogItem.kind == "product",
            CatalogItem.is_stock_tracked.is_(True),
            CatalogItem.status == "active",
        )
        .order_by(CatalogItem.name)
    )
    return [CatalogItemOut.model_validate(r) for r in rows]


def search_items(
    db: Session, ctx: Context, query: str | None, kind: str | None, limit: int = 20
) -> list[CatalogItemOut]:
    rows, _ = service.search_items(db, ctx, query=query, kind=kind, limit=limit)
    return [CatalogItemOut.model_validate(r) for r in rows]


def get_service_requirements(db: Session, ctx: Context, service_id: uuid.UUID) -> ServiceRequirements:
    return service.requirements(db, ctx, service_id)


def list_active_service_requirements(db: Session, ctx: Context) -> list[ServiceRequirements]:
    """All active services and their typed requirements for trusted intake context (no contact data)."""
    from sqlalchemy import select

    from .models import CatalogItem

    ctx.require("catalog.item.read")
    ids = list(
        db.scalars(
            select(CatalogItem.id)
            .where(
                CatalogItem.business_id == ctx.business_id,
                CatalogItem.kind == "service",
                CatalogItem.status == "active",
            )
            .order_by(CatalogItem.name, CatalogItem.id)
        )
    )
    return [get_service_requirements(db, ctx, item_id) for item_id in ids]


__all__ = [
    "CatalogItemOut",
    "PublicServiceOut",
    "ServicePartOut",
    "ServiceRequirements",
    "get_default_currency",
    "get_item",
    "get_items",
    "get_service_requirements",
    "list_active_service_requirements",
    "search_items",
]


def get_default_currency(db: Session, ctx: Context) -> str:
    """Currency for new catalog-priced documents, including free-text-only sales drafts."""
    from app.modules.org.public import get_business_brief

    return get_business_brief(db, ctx).currency
