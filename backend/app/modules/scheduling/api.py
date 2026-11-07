"""/api/v1/scheduling and /api/v1/public/{slug}/appointment-requests."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.conversations import public as conversations
from app.modules.org import public as org
from app.platform import audit, crud
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import NotFound, error_responses
from app.platform.permissions import public_context, require

from . import permissions as P
from . import service
from .models import AvailabilityException, Resource
from .schemas import (
    AppointmentOut,
    BookingRequest,
    CancelIn,
    ExceptionIn,
    ExceptionOut,
    HoldIn,
    PublicRequestIn,
    PublicRequestOut,
    RescheduleIn,
    ResourceIn,
    ResourceOut,
    ResourceUpdate,
    RuleIn,
    SlotOut,
    SlotQuery,
    VersionIn,
)

router = APIRouter()
public_router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


class ResourceDetail(ResourceOut):
    rules: list[RuleIn]
    service_ids: list[uuid.UUID]


def _detail(db: Session, ctx: Context, r: Resource) -> ResourceDetail:
    return ResourceDetail(
        **ResourceOut.model_validate(r).model_dump(),
        rules=[RuleIn.model_validate(x) for x in service.rules_of(db, ctx, r.id)],
        service_ids=service.services_of(db, ctx, r.id),
    )


def _one(db: Session, ctx: Context, a: object) -> AppointmentOut:
    return AppointmentOut(**service.enrich(db, ctx, [a])[0])  # type: ignore[list-item]


@router.get("/resources", response_model=list[ResourceDetail], summary="Bookable resources", responses=ERRORS)
def resources(include_archived: bool = False, ctx: Context = Depends(require(P.RESOURCE_READ))) -> list[ResourceDetail]:
    with transaction(ctx) as db:
        return [_detail(db, ctx, r) for r in service.list_resources(db, ctx, include_archived)]


@router.post(
    "/resources", response_model=ResourceDetail, status_code=201, summary="Create a resource", responses=ERRORS
)
def create_resource(body: ResourceIn, ctx: Context = Depends(require(P.RESOURCE_WRITE))) -> ResourceDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.create_resource(db, ctx, body))


@router.patch("/resources/{resource_id}", response_model=ResourceDetail, summary="Update a resource", responses=ERRORS)
def update_resource(
    resource_id: uuid.UUID, body: ResourceUpdate, ctx: Context = Depends(require(P.RESOURCE_WRITE))
) -> ResourceDetail:
    with transaction(ctx) as db:
        return _detail(db, ctx, service.update_resource(db, ctx, resource_id, body))


@router.put(
    "/resources/{resource_id}/availability",
    response_model=ResourceDetail,
    summary="Replace weekly availability",
    responses=ERRORS,
)
def put_rules(
    resource_id: uuid.UUID, body: list[RuleIn], ctx: Context = Depends(require(P.RESOURCE_WRITE))
) -> ResourceDetail:
    with transaction(ctx) as db:
        service.replace_rules(db, ctx, resource_id, body)
        return _detail(db, ctx, crud.require_scoped(db, ctx, Resource, resource_id, "scheduling.resource_not_found"))


@router.put(
    "/resources/{resource_id}/services",
    response_model=ResourceDetail,
    summary="Services this resource can perform",
    responses=ERRORS,
)
def put_services(
    resource_id: uuid.UUID, body: list[uuid.UUID], ctx: Context = Depends(require(P.RESOURCE_WRITE))
) -> ResourceDetail:
    with transaction(ctx) as db:
        service.replace_services(db, ctx, resource_id, body)
        return _detail(db, ctx, crud.require_scoped(db, ctx, Resource, resource_id, "scheduling.resource_not_found"))


@router.post(
    "/exceptions", response_model=ExceptionOut, status_code=201, summary="Closure or extra opening", responses=ERRORS
)
def add_exception(body: ExceptionIn, ctx: Context = Depends(require(P.RESOURCE_WRITE))) -> ExceptionOut:
    with transaction(ctx) as db:
        return ExceptionOut.model_validate(service.add_exception(db, ctx, body))


@router.get("/exceptions", response_model=list[ExceptionOut], summary="Closures and extra openings", responses=ERRORS)
def list_exceptions(ctx: Context = Depends(require(P.RESOURCE_READ))) -> list[ExceptionOut]:
    from sqlalchemy import select

    with transaction(ctx) as db:
        rows = db.scalars(
            select(AvailabilityException)
            .where(AvailabilityException.business_id == ctx.business_id)
            .order_by(AvailabilityException.starts_at.desc())
            .limit(200)
        )
        return [ExceptionOut.model_validate(r) for r in rows]


@router.get(
    "/slots", response_model=list[SlotOut], summary="Free slots for a service (deterministic)", responses=ERRORS
)
def slots(
    service_id: uuid.UUID,
    earliest: datetime,
    latest: datetime,
    resource_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    ctx: Context = Depends(require(P.APPOINTMENT_READ)),
) -> list[SlotOut]:
    with transaction(ctx) as db:
        return service.find_slots(
            db,
            ctx,
            SlotQuery(
                service_id=service_id,
                earliest=earliest,
                latest=latest,
                resource_id=resource_id,
                location_id=location_id,
                limit=limit,
            ),
        )


@router.get(
    "/appointments", response_model=list[AppointmentOut], summary="Appointments in a time window", responses=ERRORS
)
def list_appointments(
    start: datetime,
    end: datetime,
    resource_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    status: str | None = None,
    ctx: Context = Depends(require(P.APPOINTMENT_READ)),
) -> list[AppointmentOut]:
    with transaction(ctx) as db:
        rows = service.list_appointments(
            db, ctx, start=start, end=end, resource_id=resource_id, customer_id=customer_id, status=status
        )
        return [AppointmentOut(**r) for r in service.enrich(db, ctx, rows)]


@router.post(
    "/holds", response_model=AppointmentOut, status_code=201, summary="Hold a slot during review", responses=ERRORS
)
def place_hold(body: HoldIn, ctx: Context = Depends(require(P.APPOINTMENT_WRITE))) -> AppointmentOut:
    with transaction(ctx) as db:
        hold = service.place_hold(
            db,
            ctx,
            service_id=body.service_id,
            resource_id=body.resource_id,
            starts_at=body.starts_at,
            customer_id=body.customer_id,
        )
        return _one(db, ctx, hold)


@router.post("/appointments", response_model=AppointmentOut, status_code=201, summary="Book (staff)", responses=ERRORS)
def book(body: BookingRequest, ctx: Context = Depends(require(P.APPOINTMENT_WRITE))) -> AppointmentOut:
    with transaction(ctx) as db:
        return _one(db, ctx, service.book(db, ctx, body))


@router.get(
    "/appointments/{appointment_id}", response_model=AppointmentOut, summary="One appointment", responses=ERRORS
)
def get_appointment(appointment_id: uuid.UUID, ctx: Context = Depends(require(P.APPOINTMENT_READ))) -> AppointmentOut:
    from .models import Appointment

    with transaction(ctx) as db:
        result = _one(
            db, ctx, crud.require_scoped(db, ctx, Appointment, appointment_id, "scheduling.appointment_not_found")
        )
        audit.record_view(
            db, ctx, action="scheduling.appointment.viewed", target_type="appointment", target_id=appointment_id
        )
        return result


@router.post(
    "/appointments/{appointment_id}/cancel",
    response_model=AppointmentOut,
    summary="Cancel (releases parts)",
    responses=ERRORS,
)
def cancel(
    appointment_id: uuid.UUID, body: CancelIn, ctx: Context = Depends(require(P.APPOINTMENT_WRITE))
) -> AppointmentOut:
    with transaction(ctx) as db:
        return _one(db, ctx, service.cancel(db, ctx, appointment_id, body.version, body.reason))


@router.post(
    "/appointments/{appointment_id}/reschedule",
    response_model=AppointmentOut,
    summary="Move to another slot",
    responses=ERRORS,
)
def reschedule(
    appointment_id: uuid.UUID, body: RescheduleIn, ctx: Context = Depends(require(P.APPOINTMENT_WRITE))
) -> AppointmentOut:
    with transaction(ctx) as db:
        return _one(
            db,
            ctx,
            service.reschedule(
                db, ctx, appointment_id, body.version, body.starts_at, body.resource_id, body.override_hours
            ),
        )


@router.post(
    "/appointments/{appointment_id}/complete",
    response_model=AppointmentOut,
    summary="Mark done (consumes parts)",
    responses=ERRORS,
)
def complete(
    appointment_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.APPOINTMENT_WRITE))
) -> AppointmentOut:
    with transaction(ctx) as db:
        return _one(db, ctx, service.complete(db, ctx, appointment_id, body.version))


@router.post(
    "/appointments/{appointment_id}/no-show",
    response_model=AppointmentOut,
    summary="Mark no-show (releases parts)",
    responses=ERRORS,
)
def no_show(
    appointment_id: uuid.UUID, body: VersionIn, ctx: Context = Depends(require(P.APPOINTMENT_WRITE))
) -> AppointmentOut:
    with transaction(ctx) as db:
        return _one(db, ctx, service.complete(db, ctx, appointment_id, body.version, no_show=True))


@public_router.post(
    "/{business_slug}/appointment-requests",
    response_model=PublicRequestOut,
    status_code=201,
    summary="Request an appointment (visitor). Creates a conversation; never books directly.",
    responses=error_responses(403, 404, 422, 429),
)
def appointment_request(
    business_slug: str,
    body: PublicRequestIn,
    request: Request,
    response: Response,
    ctx: Context = Depends(public_context),
) -> PublicRequestOut:
    with transaction(ctx) as db:
        conversations.require_public_intake(db, ctx)
        conversations.limit_public_creation(db, request.client.host if request.client else "unknown")
        item = catalog.get_items(db, ctx, {body.service_id}).get(body.service_id)
        if item is None or item.kind != "service" or item.status != "active" or not item.is_bookable_online:
            raise NotFound("catalog.service_not_found")
        svc = catalog.get_service_requirements(db, ctx, body.service_id)
        tz = ZoneInfo(org.get_business_brief(db, ctx).timezone)
        windows = "; ".join(f"{w.date:%a %d %b} {w.time_from:%H:%M}–{w.time_to:%H:%M}" for w in body.preferred)
        text = (
            f"Appointment request: {svc.name}.\nPreferred ({tz.key} time): {windows}.\n"
            f"Contact: {body.name}"
            + (f", {body.email}" if body.email else "")
            + (f", {body.phone}" if body.phone else "")
            + (f"\nNote: {body.note}" if body.note else "")
        )
        conversation_id = conversations.start_conversation(
            db,
            ctx,
            channel="web_chat",
            body=text,
            author_kind="visitor",
            subject=f"Request: {svc.name}"[:200],
            visitor_name=body.name,
            visitor_email=body.email,
            visitor_phone=body.phone,
            consent_to_contact=body.consent_to_contact,
        )
        token = conversations.issue_visitor_token(db, ctx, conversation_id)
    conversations.set_visitor_cookie(response, business_slug, token)
    return PublicRequestOut(
        received=True, message="Thanks! We'll confirm a time with you shortly.", reply_url=f"/p/{business_slug}/chat"
    )
