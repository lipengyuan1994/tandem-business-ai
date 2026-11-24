"""Staff endpoints for supplier bills, bill payments and payables aging."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, Query

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.pagination import Page
from app.platform.permissions import require

from . import bills
from . import permissions as P
from .api import ERRORS, creating_response
from .payables import aging
from .schemas import VersionIn
from .schemas_bills import (
    BillIn,
    BillOut,
    BillPaymentIn,
    BillStatus,
    BillUpdate,
    BillVoidIn,
    PayablesAgingReport,
    PurchaseOrderBilling,
)

router = APIRouter()


@router.get("/bills", response_model=Page[BillOut], summary="List supplier bills", responses=ERRORS)
def list_bills(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    status: list[BillStatus] | None = Query(default=None),
    supplier_id: uuid.UUID | None = None,
    purchase_order_id: uuid.UUID | None = None,
    ctx: Context = Depends(require(P.BILL_READ)),
) -> Page[BillOut]:
    with transaction(ctx) as db:
        items, next_cursor = bills.listing(
            db, ctx, limit, cursor, statuses=status, supplier_id=supplier_id, purchase_order_id=purchase_order_id
        )
        return Page(items=items, next_cursor=next_cursor)


@router.post(
    "/bills", response_model=BillOut, status_code=201, summary="Record a bill from a supplier", responses=ERRORS
)
def create_bill(
    body: BillIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: Context = Depends(require(P.BILL_WRITE)),
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db, ctx, idempotency_key, "bill.create", body, lambda: bills.detail(db, ctx, bills.create(db, ctx, body))
        )


@router.get(
    "/bills/{bill_id}", response_model=BillOut, summary="Read a supplier bill and its payments", responses=ERRORS
)
def get_bill(bill_id: uuid.UUID, ctx: Context = Depends(require(P.BILL_READ))) -> BillOut:
    with transaction(ctx) as db:
        return bills.detail(db, ctx, bills.get(db, ctx, bill_id))


@router.patch(
    "/bills/{bill_id}", response_model=BillOut, summary="Edit an open supplier bill with no payments", responses=ERRORS
)
def update_bill(bill_id: uuid.UUID, body: BillUpdate, ctx: Context = Depends(require(P.BILL_WRITE))) -> BillOut:
    with transaction(ctx) as db:
        return bills.detail(db, ctx, bills.update(db, ctx, bill_id, body))


@router.post(
    "/bills/{bill_id}/void", response_model=BillOut, summary="Void a supplier bill with no payments", responses=ERRORS
)
def void_bill(bill_id: uuid.UUID, body: BillVoidIn, ctx: Context = Depends(require(P.BILL_VOID))) -> BillOut:
    with transaction(ctx) as db:
        return bills.detail(db, ctx, bills.void(db, ctx, bill_id, body))


@router.post(
    "/bills/{bill_id}/payments",
    response_model=BillOut,
    status_code=201,
    summary="Record a full or partial payment to a supplier against a bill",
    responses=ERRORS,
)
def record_bill_payment(
    bill_id: uuid.UUID,
    body: BillPaymentIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: Context = Depends(require(P.BILL_PAY)),
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            f"bill.{bill_id}.payment",
            body,
            lambda: bills.detail(db, ctx, bills.record_payment(db, ctx, bill_id, body)),
        )


@router.post(
    "/bills/{bill_id}/payments/{payment_id}/void",
    response_model=BillOut,
    summary="Void a bill payment recorded in error and reopen the bill balance",
    responses=ERRORS,
)
def void_bill_payment(
    bill_id: uuid.UUID, payment_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.BILL_VOID))
) -> BillOut:
    with transaction(ctx) as db:
        return bills.detail(db, ctx, bills.void_payment(db, ctx, bill_id, payment_id, body.version))


@router.get(
    "/purchase-orders/{purchase_order_id}/billing",
    response_model=PurchaseOrderBilling,
    summary="Received and billed amounts of a purchase order",
    responses=ERRORS,
)
def purchase_order_billing(
    purchase_order_id: uuid.UUID, ctx: Context = Depends(require(P.BILL_READ))
) -> PurchaseOrderBilling:
    with transaction(ctx) as db:
        return bills.po_billing(db, ctx, purchase_order_id)


@router.get(
    "/payables/aging",
    response_model=PayablesAgingReport,
    summary="Accounts payable aging by supplier (defaults to today in the business timezone)",
    responses=ERRORS,
)
def payables_aging(as_of: date | None = None, ctx: Context = Depends(require(P.BILL_READ))) -> PayablesAgingReport:
    with transaction(ctx) as db:
        return aging(db, ctx, as_of if as_of is not None else bills.business_today(db, ctx))
