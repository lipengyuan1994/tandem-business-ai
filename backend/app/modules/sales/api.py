"""Staff sales endpoints. Every mutation runs the service in one caller-owned transaction."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.platform import idempotency
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.pagination import Page
from app.platform.permissions import require

from . import permissions as P
from . import repository as repo
from . import service
from . import service_creation as creation
from .reminders import business_today
from .schemas import (
    DocumentCreate,
    DocumentOut,
    DocumentType,
    DocumentUpdate,
    PaymentReminderIn,
    PaymentReminderPayload,
    ReminderProposalOut,
    VersionIn,
)

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


@router.post(
    "/invoices/{document_id}/reminders",
    response_model=ReminderProposalOut,
    status_code=201,
    summary="Create a staff payment reminder proposal",
    responses=ERRORS,
)
def payment_reminder(
    document_id: uuid.UUID,
    body: PaymentReminderIn,
    ctx: Context = Depends(require(P.DOCUMENT_WRITE)),
) -> ReminderProposalOut:
    with transaction(ctx) as db:
        proposal = approvals.create_proposal(
            db,
            ctx,
            action_type="sales.send_payment_reminder",
            payload=PaymentReminderPayload(
                invoice_id=document_id, invoice_version=body.version, body=body.body
            ).model_dump(mode="json"),
            title="Send invoice payment reminder",
        )
        return ReminderProposalOut.model_validate(proposal)


def creating_response(
    db: Session,
    ctx: Context,
    key: str | None,
    request_body: dict[str, Any],
    create: Callable[[], DocumentOut],
) -> DocumentOut | JSONResponse:
    """Same transaction and response-cache contract as CRM; include the operation in the request hash."""
    if key:
        cached = idempotency.lookup(db, ctx, key, request_body)
        if cached:
            return JSONResponse(cached.body, status_code=cached.status)
    detail = create()
    if key:
        idempotency.store(db, ctx, key, request_body, 201, detail.model_dump(mode="json"))
    return detail


@router.get(
    "/invoices/export.csv",
    summary="Accounting CSV (inclusive issue-date period)",
    response_class=Response,
    responses={
        **ERRORS,
        200: {"description": "Accounting CSV", "content": {"text/csv": {"schema": {"type": "string"}}}},
    },
)
def export_csv(
    start: date = Query(alias="from"),
    end: date = Query(alias="to"),
    ctx: Context = Depends(require(P.DOCUMENT_READ)),
) -> Response:
    with transaction(ctx) as db:
        content = creation.export_csv(db, ctx, start, end)
    return Response(
        content, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="invoices.csv"'}
    )


@router.post(
    "/invoices/from-appointment/{appointment_id}",
    response_model=DocumentOut,
    status_code=201,
    summary="Invoice a completed appointment",
    responses=ERRORS,
)
def from_appointment(
    appointment_id: uuid.UUID,
    idempotency_key: str | None = Header(default=None),
    ctx: Context = Depends(require(P.DOCUMENT_WRITE)),
) -> DocumentOut | JSONResponse:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            {"operation": "sales.invoice.from_appointment", "appointment_id": str(appointment_id)},
            lambda: service.detail(db, ctx, "invoice", creation.from_appointment(db, ctx, appointment_id)),
        )


def document_routes(family: DocumentType, path: str) -> None:
    """Identical typed CRUD contract for the three document families."""

    @router.get(
        path,
        response_model=Page[DocumentOut],
        summary=f"List {family} documents",
        responses=ERRORS,
        operation_id=f"sales_list_{family}",
    )
    def list_documents(
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = None,
        status: str | None = None,
        customer_id: uuid.UUID | None = None,
        overdue: bool = False,
        ctx: Context = Depends(require(P.DOCUMENT_READ)),
    ) -> Page[DocumentOut]:
        with transaction(ctx) as db:
            rows, next_cursor = repo.list_documents(
                db,
                ctx,
                family,
                limit=limit,
                cursor=cursor,
                status=status,
                customer_id=customer_id,
                overdue_as_of=business_today(db, ctx) if overdue and family == "invoice" else None,
            )
            return Page(items=[service.detail(db, ctx, family, row) for row in rows], next_cursor=next_cursor)

    @router.post(
        path,
        response_model=DocumentOut,
        status_code=201,
        summary=f"Create draft {family}",
        responses=ERRORS,
        operation_id=f"sales_create_{family}",
    )
    def create_document(
        body: DocumentCreate,
        idempotency_key: str | None = Header(default=None),
        ctx: Context = Depends(require(P.DOCUMENT_WRITE)),
    ) -> DocumentOut | JSONResponse:
        with transaction(ctx) as db:
            return creating_response(
                db,
                ctx,
                idempotency_key,
                {"operation": f"sales.{family}.create", "body": body.model_dump(mode="json")},
                lambda: service.detail(db, ctx, family, creation.create(db, ctx, family, body)),
            )

    @router.get(
        path + "/{document_id}",
        response_model=DocumentOut,
        summary=f"Get {family}",
        responses=ERRORS,
        operation_id=f"sales_get_{family}",
    )
    def get_document(document_id: uuid.UUID, ctx: Context = Depends(require(P.DOCUMENT_READ))) -> DocumentOut:
        with transaction(ctx) as db:
            return service.detail(db, ctx, family, repo.get(db, ctx, family, document_id))

    @router.patch(
        path + "/{document_id}",
        response_model=DocumentOut,
        summary=f"Edit draft {family}",
        responses=ERRORS,
        operation_id=f"sales_update_{family}",
    )
    def update_document(
        document_id: uuid.UUID, body: DocumentUpdate, ctx: Context = Depends(require(P.DOCUMENT_WRITE))
    ) -> DocumentOut:
        with transaction(ctx) as db:
            return service.detail(db, ctx, family, service.update(db, ctx, family, document_id, body))


def transition_route(family: DocumentType, path: str, action: str, permission: str) -> None:
    @router.post(
        path + "/{document_id}/" + action,
        response_model=DocumentOut,
        summary=f"{action.title()} {family}",
        responses=ERRORS,
        operation_id=f"sales_{family}_{action.replace('-', '_')}",
    )
    def transition(document_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(permission))) -> DocumentOut:
        with transaction(ctx) as db:
            return service.detail(
                db, ctx, family, service.transition(db, ctx, family, document_id, body.version, action)
            )


for family, path in (("quote", "/quotes"), ("order", "/orders"), ("invoice", "/invoices")):
    document_routes(family, path)  # type: ignore[arg-type]
    for action in service.FLOWS[family]:  # type: ignore[index]
        permission = P.MARK_PAID if action == "mark-paid" else P.DOCUMENT_ISSUE
        transition_route(family, path, action, permission)  # type: ignore[arg-type]


@router.post(
    "/quotes/{document_id}/convert",
    response_model=DocumentOut,
    status_code=201,
    summary="Accepted quote to draft order",
    responses=ERRORS,
)
def convert_quote(
    document_id: uuid.UUID,
    body: VersionIn,
    idempotency_key: str | None = Header(default=None),
    ctx: Context = Depends(require(P.DOCUMENT_WRITE)),
) -> DocumentOut | JSONResponse:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            {"operation": "sales.quote.convert", "document_id": str(document_id), "body": body.model_dump(mode="json")},
            lambda: service.detail(db, ctx, "order", service.convert_quote(db, ctx, document_id, body.version)),
        )


@router.post(
    "/orders/{document_id}/invoice",
    response_model=DocumentOut,
    status_code=201,
    summary="Confirmed or fulfilled order to draft invoice",
    responses=ERRORS,
)
def invoice_order(
    document_id: uuid.UUID,
    body: VersionIn,
    idempotency_key: str | None = Header(default=None),
    ctx: Context = Depends(require(P.DOCUMENT_WRITE)),
) -> DocumentOut | JSONResponse:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            {"operation": "sales.order.invoice", "document_id": str(document_id), "body": body.model_dump(mode="json")},
            lambda: service.detail(db, ctx, "invoice", service.invoice_order(db, ctx, document_id, body.version)),
        )


@router.post(
    "/invoices/{document_id}/credit",
    response_model=DocumentOut,
    status_code=201,
    summary="Issue a full credit note and void the invoice",
    responses=ERRORS,
)
def credit(
    document_id: uuid.UUID,
    body: VersionIn,
    idempotency_key: str | None = Header(default=None),
    ctx: Context = Depends(require(P.CREDIT)),
) -> DocumentOut | JSONResponse:
    with transaction(ctx) as db:
        return creating_response(
            db,
            ctx,
            idempotency_key,
            {
                "operation": "sales.invoice.credit",
                "document_id": str(document_id),
                "body": body.model_dump(mode="json"),
            },
            lambda: service.detail(db, ctx, "invoice", service.credit(db, ctx, document_id, body.version)),
        )
