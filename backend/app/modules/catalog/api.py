"""/api/v1/catalog and /api/v1/public/{slug}/services."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import public_context, require

from . import permissions as P
from . import service
from .schemas import (
    CatalogItemCreate,
    CatalogItemOut,
    CatalogItemUpdate,
    Money,
    PublicServiceOut,
    ServicePartIn,
    ServicePartOut,
)

router = APIRouter()
public_router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


class ItemPage(BaseModel):
    items: list[CatalogItemOut]
    next_cursor: str | None


class VersionIn(BaseModel):
    version: int


@router.get("/items", response_model=ItemPage, summary="Search catalog items", responses=ERRORS)
def list_items(
    q: str | None = None,
    kind: Literal["service", "product"] | None = None,
    include_archived: bool = False,
    limit: int = 50,
    cursor: str | None = None,
    ctx: Context = Depends(require(P.ITEM_READ)),
) -> ItemPage:
    with transaction(ctx) as db:
        rows, nxt = service.search_items(
            db, ctx, query=q, kind=kind, include_archived=include_archived, limit=limit, cursor=cursor
        )
        return ItemPage(items=[CatalogItemOut.model_validate(r) for r in rows], next_cursor=nxt)


@router.post(
    "/items", response_model=CatalogItemOut, status_code=201, summary="Create a service or product", responses=ERRORS
)
def create_item(body: CatalogItemCreate, ctx: Context = Depends(require(P.ITEM_WRITE))) -> CatalogItemOut:
    with transaction(ctx) as db:
        return CatalogItemOut.model_validate(service.create_item(db, ctx, body))


@router.get("/items/{item_id}", response_model=CatalogItemOut, summary="Get an item", responses=ERRORS)
def get_item(item_id: uuid.UUID, ctx: Context = Depends(require(P.ITEM_READ))) -> CatalogItemOut:
    with transaction(ctx) as db:
        return CatalogItemOut.model_validate(service.get_item(db, ctx, item_id))


@router.patch("/items/{item_id}", response_model=CatalogItemOut, summary="Update an item", responses=ERRORS)
def update_item(
    item_id: uuid.UUID, body: CatalogItemUpdate, ctx: Context = Depends(require(P.ITEM_WRITE))
) -> CatalogItemOut:
    with transaction(ctx) as db:
        return CatalogItemOut.model_validate(service.update_item(db, ctx, item_id, body))


@router.post("/items/{item_id}/archive", response_model=CatalogItemOut, summary="Archive an item", responses=ERRORS)
def archive_item(item_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.ITEM_WRITE))) -> CatalogItemOut:
    with transaction(ctx) as db:
        return CatalogItemOut.model_validate(service.archive_item(db, ctx, item_id, body.version))


@router.get(
    "/items/{item_id}/parts", response_model=list[ServicePartOut], summary="Parts a service consumes", responses=ERRORS
)
def get_parts(item_id: uuid.UUID, ctx: Context = Depends(require(P.ITEM_READ))) -> list[ServicePartOut]:
    with transaction(ctx) as db:
        service.get_item(db, ctx, item_id)
        return service.get_parts(db, ctx, item_id)


@router.put(
    "/items/{item_id}/parts", response_model=list[ServicePartOut], summary="Replace a service's parts", responses=ERRORS
)
def replace_parts(
    item_id: uuid.UUID, body: list[ServicePartIn], ctx: Context = Depends(require(P.ITEM_WRITE))
) -> list[ServicePartOut]:
    with transaction(ctx) as db:
        return service.replace_parts(db, ctx, item_id, body)


@public_router.get(
    "/{business_slug}/services", response_model=list[PublicServiceOut], summary="Services bookable online"
)
def public_services(ctx: Context = Depends(public_context)) -> list[PublicServiceOut]:
    with transaction(ctx) as db:
        return [
            PublicServiceOut(
                id=s.id,
                name=s.name,
                description=s.description,
                duration_minutes=s.duration_minutes or 0,
                price=Money(amount_minor=s.price_minor, currency=s.currency) if s.price_minor is not None else None,
            )
            for s in service.public_services(db, ctx)
        ]
