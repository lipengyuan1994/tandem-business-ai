"""Scheduling rules (M06). The database exclusion constraint is the final guard against double booking."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.crm import public as crm
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.platform import audit, clock, crud, events
from app.platform.context import Context
from app.platform.errors import Conflict, Forbidden
from app.platform.events import DomainEventPayload
from app.platform.ids import new_id

from . import availability as av
from . import permissions as P
from .holds import _allocate as _allocate
from .holds import _allocation as _allocation
from .holds import _insert_guarded as _insert_guarded
from .holds import _release_expired_overlapping as _release_expired_overlapping
from .holds import bind_hold as bind_hold
from .holds import place_hold as place_hold
from .models import Appointment, AppointmentResource
from .reads import agenda_for_day as agenda_for_day
from .reads import customer_ids_with_service as customer_ids_with_service
from .reads import enrich as enrich
from .reads import ensure_found as ensure_found
from .reads import job_release_expired_holds as job_release_expired_holds
from .reads import job_release_holds_scan as job_release_holds_scan
from .reads import list_appointments as list_appointments
from .reads import on_proposal_closed as on_proposal_closed
from .resources import _resource as _resource
from .resources import add_exception as add_exception
from .resources import create_resource as create_resource
from .resources import list_resources as list_resources
from .resources import replace_rules as replace_rules
from .resources import replace_services as replace_services
from .resources import rules_of as rules_of
from .resources import services_of as services_of
from .resources import update_resource as update_resource
from .schemas import BookingRequest
from .slots import _offered as _offered
from .slots import find_slots as find_slots

ACTIVE = ("held", "confirmed")


class AppointmentEvent(DomainEventPayload):
    event_type = "scheduling.appointment_changed"
    appointment_id: uuid.UUID
    customer_id: uuid.UUID
    status: str


class AppointmentConfirmed(AppointmentEvent):
    event_type = "scheduling.appointment_confirmed"


class AppointmentCancelled(AppointmentEvent):
    event_type = "scheduling.appointment_cancelled"


class AppointmentRescheduled(AppointmentEvent):
    event_type = "scheduling.appointment_rescheduled"


class AppointmentCompleted(AppointmentEvent):
    event_type = "scheduling.appointment_completed"
    completed_at: datetime = Field(default_factory=clock.now)


def _event(db: Session, ctx: Context, cls: type[AppointmentEvent], a: Appointment) -> None:
    events.publish(db, ctx, cls(appointment_id=a.id, customer_id=a.customer_id, status=a.status))


def _timeline(db: Session, ctx: Context, a: Appointment, kind_word: str, service_name: str) -> None:
    crm.add_timeline_entry(
        db,
        ctx,
        crm.TimelineEntryIn(
            customer_id=a.customer_id,
            occurred_at=clock.now(),
            kind="appointment",
            ref_module="scheduling",
            ref_type=f"appointment_{kind_word}",
            ref_id=a.id,
            # The timeline summary is limited to 200 characters; service names may use up to 200 on their own.
            summary=f"{service_name[:120]} {kind_word}: {av.local_label(a.starts_at, ZoneInfo(a.timezone))}",
        ),
    )


def book(db: Session, ctx: Context, req: BookingRequest) -> Appointment:
    """Direct staff booking and the book_appointment executor share this function (one place for the rules)."""
    ctx.require(P.APPOINTMENT_WRITE)
    if (req.override_hours or req.skip_parts) and not ctx.has(P.APPOINTMENT_OVERRIDE):
        raise Forbidden("auth.permission_denied", detail=P.APPOINTMENT_OVERRIDE)
    service = catalog.get_service_requirements(db, ctx, req.service_id)
    resource = _resource(db, ctx, req.resource_id)
    if resource.status != "active":
        raise Conflict("scheduling.resource_not_qualified")
    customer = crm.get_booking_customer(db, ctx, req.customer_id)
    if customer.status != "active":
        raise Conflict("crm.customer_archived")
    ends = req.starts_at + timedelta(minutes=service.duration_minutes)
    blocked = ends + timedelta(minutes=service.buffer_minutes)
    hold = None
    if req.hold_id:
        hold = db.scalar(
            select(Appointment)
            .where(Appointment.business_id == ctx.business_id, Appointment.id == req.hold_id)
            .with_for_update()
        )
        valid = (
            hold is not None
            and hold.status == "held"
            and hold.hold_expires_at is not None
            and hold.hold_expires_at > clock.now()
            and hold.resource_id == req.resource_id
            and hold.starts_at == req.starts_at
            and hold.service_id == req.service_id
        )
        if not valid:
            if hold is not None and hold.status == "held":
                hold.status, hold.cancelled_reason = "cancelled", "hold expired"
                db.flush()
            hold = None
    slot = None
    if hold is None or service.required_resources:
        slot = _allocation(
            db,
            ctx,
            req.service_id,
            req.resource_id,
            req.starts_at,
            ignore=hold.id if hold else None,
            override=req.override_hours,
            ignore_lead_time=hold is not None,
        )
    _release_expired_overlapping(db, ctx, req.resource_id, req.starts_at, blocked)
    if hold is not None:
        # Re-check the current service requirements and duration with this hold ignored.
        db.execute(
            delete(AppointmentResource).where(
                AppointmentResource.business_id == ctx.business_id,
                AppointmentResource.appointment_id == hold.id,
                AppointmentResource.resource_id != hold.resource_id,
            )
        )
        appointment = hold
        appointment.status, appointment.hold_expires_at, appointment.customer_id = "confirmed", None, req.customer_id
        appointment.notes = req.notes
        appointment.version += 1
        # The service's duration or buffer may have changed since the hold was placed: block the current interval
        # (the exclusion constraint rejects it if that now overlaps another booking).
        appointment.ends_at, appointment.blocked_until = ends, blocked
        try:
            with db.begin_nested():
                db.flush()
                _allocate(db, appointment, slot)
                db.flush()
        except (IntegrityError, DBAPIError) as exc:
            raise Conflict("scheduling.slot_unavailable") from exc
    else:
        assert ctx.business_id is not None
        appointment = Appointment(
            id=new_id(),
            business_id=ctx.business_id,
            customer_id=req.customer_id,
            service_id=req.service_id,
            resource_id=req.resource_id,
            location_id=resource.location_id,
            starts_at=req.starts_at,
            ends_at=ends,
            blocked_until=blocked,
            timezone=org.get_location(db, ctx, resource.location_id).timezone,
            status="confirmed",
            proposal_id=ctx.proposal_id,
            source="assistant" if ctx.proposal_id else "staff",
            notes=req.notes,
        )
        _insert_guarded(db, appointment, slot)
    if not req.skip_parts:
        for part in sorted(service.parts, key=lambda part: part.item_id):
            inventory.reserve_stock(
                db, ctx, item_id=part.item_id, quantity=part.quantity, ref_type="appointment", ref_id=appointment.id
            )
    _timeline(db, ctx, appointment, "booked", service.name)
    audit.record(
        db,
        ctx,
        action="scheduling.appointment.confirmed",
        target_type="appointment",
        target_id=appointment.id,
        summary=f"{service.name} booked for {customer.display_name}: "
        f"{av.local_label(req.starts_at, ZoneInfo(appointment.timezone))}"
        + (" (hours overridden)" if req.override_hours else "")
        + (" (parts skipped)" if req.skip_parts else ""),
    )
    _event(db, ctx, AppointmentConfirmed, appointment)
    return appointment


def _appt(db: Session, ctx: Context, appointment_id: uuid.UUID, version: int) -> Appointment:
    observed = crud.require_scoped(db, ctx, Appointment, appointment_id, "scheduling.appointment_not_found")
    customer_id = observed.customer_id
    # Booking takes the customer before appointment/stock locks. Cancellation writes the same
    # customer's timeline after releasing stock, so it must follow that order too.
    crm.lock_customers(db, ctx, {customer_id})
    a = db.scalar(
        select(Appointment)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.id == appointment_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert a is not None
    if a.customer_id != customer_id or a.version != version:
        raise Conflict("scheduling.appointment_version_conflict", current_version=a.version)
    return a


def cancel(db: Session, ctx: Context, appointment_id: uuid.UUID, version: int, reason: str) -> Appointment:
    ctx.require(P.APPOINTMENT_WRITE)
    a = _appt(db, ctx, appointment_id, version)
    if a.status not in ACTIVE:
        raise Conflict("scheduling.not_active", current_status=a.status)
    a.status, a.cancelled_reason, a.version = "cancelled", reason or None, a.version + 1
    inventory.release_reservations(
        db, ctx, ref_type="appointment", ref_id=a.id
    )  # no stock movement: nothing physically moved
    name = catalog.get_items(db, ctx, {a.service_id})[a.service_id].name
    _timeline(db, ctx, a, "cancelled", name)
    audit.record(
        db,
        ctx,
        action="scheduling.appointment.cancelled",
        target_type="appointment",
        target_id=a.id,
        summary=f"Cancelled: {reason or 'no reason'}",
    )
    _event(db, ctx, AppointmentCancelled, a)
    return a


def reschedule(
    db: Session,
    ctx: Context,
    appointment_id: uuid.UUID,
    version: int,
    new_start: datetime,
    resource_id: uuid.UUID | None = None,
    override_hours: bool = False,
) -> Appointment:
    ctx.require(P.APPOINTMENT_WRITE)
    if override_hours and not ctx.has(P.APPOINTMENT_OVERRIDE):
        raise Forbidden("auth.permission_denied", detail=P.APPOINTMENT_OVERRIDE)
    a = _appt(db, ctx, appointment_id, version)
    if a.status != "confirmed":
        raise Conflict("scheduling.not_active", current_status=a.status)
    if crm.is_restricted(db, ctx, a.customer_id):
        raise Conflict("crm.customer_restricted", detail="This customer is restricted; booking changes are blocked")
    target_resource = resource_id or a.resource_id
    slot = _allocation(db, ctx, a.service_id, target_resource, new_start, ignore=a.id, override=override_hours)
    shift = new_start - a.starts_at
    # Expired holds are only cancelled by a once-a-minute job; free the target interval like booking does.
    _release_expired_overlapping(db, ctx, target_resource, new_start, a.blocked_until + shift)
    old_label = av.local_label(a.starts_at, ZoneInfo(a.timezone))
    try:
        with db.begin_nested():
            db.execute(
                delete(AppointmentResource).where(
                    AppointmentResource.business_id == ctx.business_id,
                    AppointmentResource.appointment_id == a.id,
                    AppointmentResource.resource_id != a.resource_id,
                )
            )
            a.starts_at, a.ends_at, a.blocked_until = new_start, a.ends_at + shift, a.blocked_until + shift
            resource = _resource(db, ctx, target_resource)
            a.location_id = resource.location_id
            a.timezone = org.get_location(db, ctx, resource.location_id).timezone
            a.resource_id, a.version = target_resource, a.version + 1
            db.flush()
            _allocate(db, a, slot)
            db.flush()
    except (IntegrityError, DBAPIError) as exc:
        raise Conflict("scheduling.slot_unavailable") from exc
    audit.record(
        db,
        ctx,
        action="scheduling.appointment.rescheduled",
        target_type="appointment",
        target_id=a.id,
        summary=f"Moved from {old_label} to {av.local_label(new_start, ZoneInfo(a.timezone))}",
    )
    _event(db, ctx, AppointmentRescheduled, a)
    return a


def complete(
    db: Session, ctx: Context, appointment_id: uuid.UUID, version: int, *, no_show: bool = False
) -> Appointment:
    ctx.require(P.APPOINTMENT_WRITE)
    a = _appt(db, ctx, appointment_id, version)
    if a.status != "confirmed":
        raise Conflict("scheduling.not_active", current_status=a.status)
    a.status, a.version = ("no_show" if no_show else "completed"), a.version + 1
    if no_show:
        inventory.release_reservations(db, ctx, ref_type="appointment", ref_id=a.id)
    else:
        inventory.consume_reservations(db, ctx, ref_type="appointment", ref_id=a.id)
    audit.record(
        db,
        ctx,
        action=f"scheduling.appointment.{a.status}",
        target_type="appointment",
        target_id=a.id,
        summary=a.status.replace("_", " ").title(),
    )
    _event(db, ctx, AppointmentCompleted, a)
    return a
