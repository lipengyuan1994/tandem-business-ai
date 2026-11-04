"""Authenticated manual procurement flow. Writes create staff proposals for the registered executors."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.platform import crud
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Invalid, error_responses
from app.platform.pagination import Page
from app.platform.permissions import require

from . import permissions as P
from . import quote_reads as reads
from . import quote_service as service
from .api import creating_response
from .models import QuoteRequest
from .schemas_quotes import (
    CreatePOFromQuote,
    LatePO,
    QuoteComparison,
    QuoteLinkOut,
    QuoteRequestOut,
    RecordSupplierQuote,
    SendQuoteRequests,
    StaffProposalOut,
    SupplierFollowup,
    SupplierOption,
)

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


def propose(db: Session, ctx: Context, action: str, payload: Any, title: str) -> StaffProposalOut:
    return StaffProposalOut.model_validate(
        approvals.create_proposal(
            db, ctx, action_type="purchasing." + action, payload=payload.model_dump(mode="json"), title=title
        )
    )


@router.post(
    "/quote-requests",
    response_model=StaffProposalOut,
    status_code=201,
    summary="Propose sending supplier quote requests",
    responses=ERRORS,
)
def send_requests(
    body: SendQuoteRequests,
    idempotency_key: str | None = Header(default=None),
    ctx: Context = Depends(require(P.WRITE)),
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            "quote_request.send",
            body,
            lambda: propose(db, ctx, "send_quote_requests", body, "Send supplier quote requests"),
        )


@router.get("/quote-requests", response_model=Page[QuoteRequestOut], summary="List quote requests", responses=ERRORS)
def list_requests(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    status: str | None = None,
    ctx: Context = Depends(require(P.READ)),
) -> Page[QuoteRequestOut]:
    with transaction(ctx) as db:
        stmt = select(QuoteRequest).where(QuoteRequest.business_id == ctx.business_id)
        if status is not None:
            stmt = stmt.where(QuoteRequest.status == status)
        rows, next_cursor = crud.page(db, stmt, QuoteRequest, limit, cursor)
        return Page(items=[reads.get_quote_request(db, ctx, r.id) for r in rows], next_cursor=next_cursor)


@router.get(
    "/quote-requests/{quote_request_id}",
    response_model=QuoteRequestOut,
    summary="Read request, invites and quotes",
    responses=ERRORS,
)
def get_request(quote_request_id: uuid.UUID, ctx: Context = Depends(require(P.READ))) -> QuoteRequestOut:
    with transaction(ctx) as db:
        return reads.get_quote_request(db, ctx, quote_request_id)


@router.post(
    "/quote-requests/{quote_request_id}/quotes",
    response_model=StaffProposalOut,
    status_code=201,
    summary="Propose a manually entered supplier quote",
    responses=ERRORS,
)
def record_quote(
    quote_request_id: uuid.UUID,
    body: RecordSupplierQuote,
    idempotency_key: str | None = Header(default=None),
    ctx: Context = Depends(require(P.WRITE)),
) -> Any:
    if body.quote_request_id != quote_request_id or body.source != "staff":
        raise Invalid("purchasing.invalid_staff_quote")
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            "quote.record",
            body,
            lambda: propose(db, ctx, "record_supplier_quote", body, "Record supplier quote"),
        )


@router.post(
    "/quote-requests/{quote_request_id}/invites/{supplier_id}/link",
    response_model=QuoteLinkOut,
    summary="Copy the signed quote link of a supplier who has not answered",
    responses=ERRORS,
)
def invite_link(
    quote_request_id: uuid.UUID,
    supplier_id: uuid.UUID,
    response: Response,
    ctx: Context = Depends(require(P.WRITE)),
) -> QuoteLinkOut:
    with transaction(ctx) as db:
        url = service.copy_link(db, ctx, quote_request_id, supplier_id)
    response.headers["Cache-Control"] = "no-store"  # a capability: nothing may keep a copy
    return QuoteLinkOut(url=url)


@router.get(
    "/quote-requests/{quote_request_id}/comparison",
    response_model=QuoteComparison,
    summary="Compare supplier quotes with explained deterministic scores",
    responses=ERRORS,
)
def comparison(quote_request_id: uuid.UUID, ctx: Context = Depends(require(P.READ))) -> QuoteComparison:
    with transaction(ctx) as db:
        return reads.compare_quotes(db, ctx, quote_request_id)


@router.post(
    "/purchase-orders/from-quote",
    response_model=StaffProposalOut,
    status_code=201,
    summary="Propose a draft purchase order from a quote",
    responses=ERRORS,
)
def po_from_quote(
    body: CreatePOFromQuote,
    idempotency_key: str | None = Header(default=None),
    ctx: Context = Depends(require(P.WRITE)),
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            "po.from_quote",
            body,
            lambda: propose(db, ctx, "create_po_from_quote", body, "Create draft PO from supplier quote"),
        )


@router.post(
    "/supplier-followups",
    response_model=StaffProposalOut,
    status_code=201,
    summary="Propose a supplier followup email",
    responses=ERRORS,
)
def followup(
    body: SupplierFollowup, idempotency_key: str | None = Header(default=None), ctx: Context = Depends(require(P.WRITE))
) -> Any:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            "supplier.followup",
            body,
            lambda: propose(db, ctx, "send_supplier_followup", body, "Follow up with supplier"),
        )


@router.get("/late-purchase-orders", response_model=list[LatePO], summary="List late purchase orders", responses=ERRORS)
def late_orders(as_of: date | None = None, ctx: Context = Depends(require(P.READ))) -> list[LatePO]:
    with transaction(ctx) as db:
        return reads.list_late_purchase_orders(db, ctx, as_of=as_of)


@router.get(
    "/items/{item_id}/suppliers",
    response_model=list[SupplierOption],
    summary="Read supplier options for an item",
    responses=ERRORS,
)
def item_suppliers(item_id: uuid.UUID, ctx: Context = Depends(require(P.READ))) -> list[SupplierOption]:
    with transaction(ctx) as db:
        return reads.list_suppliers_for_item(db, ctx, item_id)
