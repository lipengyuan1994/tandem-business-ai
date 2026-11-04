from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.platform import idempotency
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.pagination import Page
from app.platform.permissions import require

from . import executors, service, service_receipts
from . import permissions as P
from . import repository as repo
from . import service_suppliers as suppliers
from .schemas import (
    DraftConversion,
    POCreate,
    POOut,
    POUpdate,
    PurchasingSuggestion,
    ReceiptIn,
    SupplierIn,
    SupplierOut,
    SupplierUpdate,
    VersionIn,
)

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


def creating_response(
    db: Session, ctx: Context, key: str | None, operation: str, body: BaseModel, create: Callable[[], Any]
) -> Any:
    request = {"operation": operation, "body": body.model_dump(mode="json")}
    if key:
        repo.lock_idempotency(db, ctx, key)
        cached = idempotency.lookup(db, ctx, key, request)
        if cached:
            return JSONResponse(cached.body, status_code=cached.status)
    result = create()
    if key:
        idempotency.store(db, ctx, key, request, 201, result.model_dump(mode="json"))
    return result


@router.get("/suppliers", response_model=Page[SupplierOut], summary="List suppliers", responses=ERRORS)
def list_suppliers(
    limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None, ctx: Context = Depends(require(P.READ))
) -> Page[SupplierOut]:
    with transaction(ctx) as db:
        rows, next_cursor = repo.suppliers(db, ctx, limit, cursor)
        return Page(items=[suppliers.detail(db, ctx, row) for row in rows], next_cursor=next_cursor)


@router.get("/suppliers/{supplier_id}", response_model=SupplierOut, summary="Read supplier", responses=ERRORS)
def get_supplier(supplier_id: uuid.UUID, ctx: Context = Depends(require(P.READ))) -> SupplierOut:
    with transaction(ctx) as db:
        return suppliers.detail(db, ctx, repo.supplier(db, ctx, supplier_id))


@router.post("/suppliers", response_model=SupplierOut, status_code=201, summary="Create supplier", responses=ERRORS)
def create_supplier(
    body: SupplierIn, idempotency_key: str | None = Header(default=None), ctx: Context = Depends(require(P.WRITE))
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            "supplier.create",
            body,
            lambda: suppliers.detail(db, ctx, suppliers.create(db, ctx, body)),
        )


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut, summary="Edit supplier", responses=ERRORS)
def update_supplier(
    supplier_id: uuid.UUID, body: SupplierUpdate, ctx: Context = Depends(require(P.WRITE))
) -> SupplierOut:
    with transaction(ctx) as db:
        return suppliers.detail(db, ctx, suppliers.update(db, ctx, supplier_id, body))


@router.get("/purchase-orders", response_model=Page[POOut], summary="List purchase orders", responses=ERRORS)
def list_orders(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    status: str | None = None,
    ctx: Context = Depends(require(P.READ)),
) -> Page[POOut]:
    with transaction(ctx) as db:
        rows, next_cursor = repo.orders(db, ctx, limit, cursor, status)
        return Page(items=[service.detail(db, ctx, row) for row in rows], next_cursor=next_cursor)


@router.post(
    "/purchase-orders", response_model=POOut, status_code=201, summary="Create purchase order draft", responses=ERRORS
)
def create_order(
    body: POCreate, idempotency_key: str | None = Header(default=None), ctx: Context = Depends(require(P.WRITE))
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db, ctx, idempotency_key, "po.create", body, lambda: service.detail(db, ctx, service.create(db, ctx, body))
        )


@router.post(
    "/purchase-orders/from-draft",
    response_model=POOut,
    status_code=201,
    summary="Convert inventory reorder draft",
    responses=ERRORS,
)
def convert_draft(
    body: DraftConversion, idempotency_key: str | None = Header(default=None), ctx: Context = Depends(require(P.WRITE))
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            "po.from_draft",
            body,
            lambda: service.detail(db, ctx, executors.from_draft(db, ctx, body)),
        )


@router.get(
    "/purchase-orders/{purchase_order_id}",
    response_model=POOut,
    summary="Read purchase order and receipts",
    responses=ERRORS,
)
def get_order(purchase_order_id: uuid.UUID, ctx: Context = Depends(require(P.READ))) -> POOut:
    with transaction(ctx) as db:
        return service.detail(db, ctx, repo.order(db, ctx, purchase_order_id))


@router.patch(
    "/purchase-orders/{purchase_order_id}", response_model=POOut, summary="Edit purchase order draft", responses=ERRORS
)
def update_order(purchase_order_id: uuid.UUID, body: POUpdate, ctx: Context = Depends(require(P.WRITE))) -> POOut:
    with transaction(ctx) as db:
        return service.detail(db, ctx, service.update(db, ctx, purchase_order_id, body))


def transition_route(action: str, permission: str) -> None:
    @router.post(
        "/purchase-orders/{purchase_order_id}/" + action,
        response_model=POOut,
        summary=f"{action.title()} purchase order",
        responses=ERRORS,
        operation_id=f"purchasing_po_{action}",
    )
    def transition(purchase_order_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(permission))) -> POOut:
        with transaction(ctx) as db:
            return service.detail(db, ctx, service.transition(db, ctx, purchase_order_id, body.version, action))


for action in ("approve", "send", "cancel"):
    transition_route(action, P.APPROVE if action == "approve" else P.WRITE)


@router.post(
    "/purchase-orders/{purchase_order_id}/receipts",
    response_model=POOut,
    status_code=201,
    summary="Receive goods",
    responses=ERRORS,
)
def receive(
    purchase_order_id: uuid.UUID,
    body: ReceiptIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: Context = Depends(require(P.RECEIVE)),
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            f"po.{purchase_order_id}.receipt",
            body,
            lambda: service.detail(db, ctx, service_receipts.receive(db, ctx, purchase_order_id, body)),
        )


@router.get(
    "/reorder-suggestions",
    response_model=list[PurchasingSuggestion],
    summary="Reorder quantities after open purchase orders",
    responses=ERRORS,
)
def suggestions(ctx: Context = Depends(require(P.READ))) -> list[PurchasingSuggestion]:
    with transaction(ctx) as db:
        return service.suggestions(db, ctx)
