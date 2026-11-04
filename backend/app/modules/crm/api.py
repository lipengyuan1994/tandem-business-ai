"""/api/v1/crm."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.platform import audit, idempotency
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require

from . import permissions as P
from . import service
from .schemas import (
    ConsentIn,
    ConsentOut,
    ContactPointIn,
    ContactPointOut,
    CustomerCandidate,
    CustomerCreate,
    CustomerDetail,
    CustomerListItem,
    CustomerOut,
    CustomerUpdate,
    NoteIn,
    NoteOut,
    TimelineEntryOut,
)

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


class CustomerPage(BaseModel):
    items: list[CustomerListItem]
    next_cursor: str | None


class VersionIn(BaseModel):
    version: int


class TagsIn(BaseModel):
    tags: list[str]


def _detail(db: Session, ctx: Context, customer_id: uuid.UUID) -> CustomerDetail:
    c = service.get_customer(db, ctx, customer_id)
    return CustomerDetail(
        **CustomerOut.model_validate(c).model_dump(),
        contact_points=[ContactPointOut.model_validate(x) for x in service.contacts_of(db, ctx, [c.id]).get(c.id, [])],
        tags=service.tags_of(db, ctx, [c.id]).get(c.id, []),
        current_consents=[ConsentOut.model_validate(x) for x in service.current_consents(db, ctx, c.id)]
        if ctx.has(P.CONSENT_READ)
        else [],
    )


@router.get("/customers", response_model=CustomerPage, summary="Search customers", responses=ERRORS)
def list_customers(
    q: str | None = None,
    tag: str | None = None,
    status: str = "active",
    limit: int = 50,
    cursor: str | None = None,
    ctx: Context = Depends(require(P.CUSTOMER_READ)),
) -> CustomerPage:
    with transaction(ctx) as db:
        rows, nxt = service.list_customers(db, ctx, q=q, tag=tag, status=status, limit=limit, cursor=cursor)
        ids = [r.id for r in rows]
        tags, contacts = service.tags_of(db, ctx, ids), service.contacts_of(db, ctx, ids)
        items = []
        for r in rows:
            cps = contacts.get(r.id, [])
            items.append(
                CustomerListItem(
                    **CustomerOut.model_validate(r).model_dump(),
                    tags=tags.get(r.id, []),
                    primary_email=next((c.value for c in cps if c.type == "email" and c.is_primary), None),
                    primary_phone=next((c.value for c in cps if c.type == "phone" and c.is_primary), None),
                )
            )
        return CustomerPage(items=items, next_cursor=nxt)


@router.post(
    "/customers", response_model=CustomerDetail, status_code=201, summary="Create a customer", responses=ERRORS
)
def create_customer(
    body: CustomerCreate,
    idempotency_key: str | None = Header(default=None),
    ctx: Context = Depends(require(P.CUSTOMER_WRITE)),
) -> CustomerDetail | JSONResponse:
    with transaction(ctx) as db:
        if idempotency_key:
            cached = idempotency.lookup(db, ctx, idempotency_key, body.model_dump(mode="json"))
            if cached:
                return JSONResponse(cached.body, status_code=cached.status)
        detail = _detail(db, ctx, service.create_customer(db, ctx, body).id)
        if idempotency_key:
            idempotency.store(
                db, ctx, idempotency_key, body.model_dump(mode="json"), 201, detail.model_dump(mode="json")
            )
        return detail


@router.get(
    "/customers/match",
    response_model=list[CustomerCandidate],
    summary="Find possible matches (never merges)",
    responses=ERRORS,
)
def match(
    email: str | None = None,
    phone: str | None = None,
    name: str | None = None,
    ctx: Context = Depends(require(P.CUSTOMER_READ)),
) -> list[CustomerCandidate]:
    with transaction(ctx) as db:
        return service.find_candidates(db, ctx, name=name, email=email, phone=phone)


@router.get(
    "/customers/{customer_id}",
    response_model=CustomerDetail,
    summary="Customer with contacts, tags, consent",
    responses=ERRORS,
)
def get_customer(customer_id: uuid.UUID, ctx: Context = Depends(require(P.CUSTOMER_READ))) -> CustomerDetail:
    with transaction(ctx) as db:
        detail = _detail(db, ctx, customer_id)
        audit.record_view(db, ctx, action="crm.customer.viewed", target_type="customer", target_id=customer_id)
        return detail


@router.patch("/customers/{customer_id}", response_model=CustomerDetail, summary="Update a customer", responses=ERRORS)
def update_customer(
    customer_id: uuid.UUID, body: CustomerUpdate, ctx: Context = Depends(require(P.CUSTOMER_WRITE))
) -> CustomerDetail:
    with transaction(ctx) as db:
        service.update_customer(db, ctx, customer_id, body.version, body)
        return _detail(db, ctx, customer_id)


@router.post(
    "/customers/{customer_id}/archive", response_model=CustomerOut, summary="Archive a customer", responses=ERRORS
)
def archive(customer_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.CUSTOMER_WRITE))) -> CustomerOut:
    with transaction(ctx) as db:
        return CustomerOut.model_validate(service.archive_customer(db, ctx, customer_id, body.version))


@router.post(
    "/customers/{customer_id}/contact-points",
    response_model=ContactPointOut,
    status_code=201,
    summary="Add email or phone",
    responses=ERRORS,
)
def add_contact(
    customer_id: uuid.UUID, body: ContactPointIn, ctx: Context = Depends(require(P.CUSTOMER_WRITE))
) -> ContactPointOut:
    with transaction(ctx) as db:
        return ContactPointOut.model_validate(service.add_contact_point(db, ctx, customer_id, body))


@router.delete(
    "/contact-points/{contact_point_id}", status_code=204, summary="Remove a contact point", responses=ERRORS
)
def remove_contact(contact_point_id: uuid.UUID, ctx: Context = Depends(require(P.CUSTOMER_WRITE))) -> None:
    with transaction(ctx) as db:
        service.remove_contact_point(db, ctx, contact_point_id)


@router.get(
    "/customers/{customer_id}/consents",
    response_model=list[ConsentOut],
    summary="Consent history (newest first)",
    responses=ERRORS,
)
def consents(customer_id: uuid.UUID, ctx: Context = Depends(require(P.CONSENT_READ))) -> list[ConsentOut]:
    with transaction(ctx) as db:
        rows = [ConsentOut.model_validate(r) for r in service.consent_history(db, ctx, customer_id)]
        audit.record_view(db, ctx, action="crm.customer.viewed", target_type="customer", target_id=customer_id)
        return rows


@router.post(
    "/customers/{customer_id}/consents",
    response_model=ConsentOut,
    status_code=201,
    summary="Record consent (append-only)",
    responses=ERRORS,
)
def add_consent(
    customer_id: uuid.UUID, body: ConsentIn, ctx: Context = Depends(require(P.CONSENT_WRITE))
) -> ConsentOut:
    with transaction(ctx) as db:
        service.get_customer(db, ctx, customer_id)
        return ConsentOut.model_validate(service.record_consent(db, ctx, customer_id, body))


@router.get("/customers/{customer_id}/notes", response_model=list[NoteOut], summary="Notes", responses=ERRORS)
def get_notes(customer_id: uuid.UUID, ctx: Context = Depends(require(P.CUSTOMER_READ))) -> list[NoteOut]:
    with transaction(ctx) as db:
        rows = [NoteOut.model_validate(n) for n in service.notes(db, ctx, customer_id)]
        audit.record_view(db, ctx, action="crm.customer.viewed", target_type="customer", target_id=customer_id)
        return rows


@router.post(
    "/customers/{customer_id}/notes", response_model=NoteOut, status_code=201, summary="Add a note", responses=ERRORS
)
def add_note(customer_id: uuid.UUID, body: NoteIn, ctx: Context = Depends(require(P.CUSTOMER_WRITE))) -> NoteOut:
    with transaction(ctx) as db:
        return NoteOut.model_validate(service.add_note(db, ctx, customer_id, body))


@router.get(
    "/customers/{customer_id}/timeline",
    response_model=list[TimelineEntryOut],
    summary="Timeline (newest first)",
    responses=ERRORS,
)
def get_timeline(
    customer_id: uuid.UUID, limit: int = 50, ctx: Context = Depends(require(P.CUSTOMER_READ))
) -> list[TimelineEntryOut]:
    with transaction(ctx) as db:
        rows = [TimelineEntryOut.model_validate(t) for t in service.timeline(db, ctx, customer_id, limit)]
        audit.record_view(db, ctx, action="crm.customer.viewed", target_type="customer", target_id=customer_id)
        return rows


@router.put(
    "/customers/{customer_id}/tags", response_model=list[str], summary="Replace a customer's tags", responses=ERRORS
)
def put_tags(customer_id: uuid.UUID, body: TagsIn, ctx: Context = Depends(require(P.CUSTOMER_WRITE))) -> list[str]:
    with transaction(ctx) as db:
        return service.set_tags(db, ctx, customer_id, body.tags)
