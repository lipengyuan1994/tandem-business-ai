"""Public API of the scheduling module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.platform.context import Context

from . import service
from .care import completed_page as completed_service_page
from .care import has_future as has_future_service_appointment
from .care import latest_completed as latest_completed_appointment
from .schemas import AppointmentOut, BookingRequest, HoldDto, SlotOut, SlotQuery
from .service import AppointmentCancelled, AppointmentCompleted, AppointmentConfirmed, AppointmentRescheduled


def customer_ids_with_service(db: Session, ctx: Context, service_id: uuid.UUID) -> set[uuid.UUID]:
    """Customers with a confirmed or completed appointment for the service (used by marketing segments)."""
    return service.customer_ids_with_service(db, ctx, service_id)


def find_slots(db: Session, ctx: Context, q: SlotQuery) -> list[SlotOut]:
    return service.find_slots(db, ctx, q)


def place_hold(
    db: Session,
    ctx: Context,
    *,
    service_id: uuid.UUID,
    resource_id: uuid.UUID,
    starts_at: datetime,
    customer_id: uuid.UUID,
    proposal_id: uuid.UUID | None = None,
) -> HoldDto:
    h = service.place_hold(
        db,
        ctx,
        service_id=service_id,
        resource_id=resource_id,
        starts_at=starts_at,
        customer_id=customer_id,
        proposal_id=proposal_id,
    )
    assert h.hold_expires_at is not None
    return HoldDto(hold_id=h.id, starts_at=h.starts_at, ends_at=h.ends_at, hold_expires_at=h.hold_expires_at)


def book(db: Session, ctx: Context, req: BookingRequest) -> AppointmentOut:
    return AppointmentOut(**service.enrich(db, ctx, [service.book(db, ctx, req)])[0])


def bind_hold(db: Session, ctx: Context, hold_id: uuid.UUID, proposal_id: uuid.UUID) -> None:
    from sqlalchemy import select

    from app.platform import clock
    from app.platform.errors import Conflict

    from .models import Appointment

    ctx.require("scheduling.appointment.write")
    # Read the scalar under a row lock: an ORM object loaded earlier in this session may be stale. The lock
    # remains held while the service validates the state/proposal and binds, including idempotent bindings.
    expires_at = db.scalar(
        select(Appointment.hold_expires_at)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.id == hold_id,
        )
        .with_for_update()
    )
    if expires_at is None or expires_at <= clock.now():
        raise Conflict("scheduling.hold_not_bindable")
    service.bind_hold(db, ctx, hold_id, proposal_id)


def list_appointments(
    db: Session,
    ctx: Context,
    *,
    start: datetime,
    end: datetime,
    resource_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
) -> list[AppointmentOut]:
    rows = service.list_appointments(db, ctx, start=start, end=end, resource_id=resource_id, customer_id=customer_id)
    return [AppointmentOut(**r) for r in service.enrich(db, ctx, rows)]


def agenda(db: Session, ctx: Context, start: datetime, end: datetime) -> list[AppointmentOut]:
    """Internal read for the dashboard (no permission check of its own)."""
    return [AppointmentOut(**r) for r in service.enrich(db, ctx, service.agenda_for_day(db, ctx, start, end))]


__all__ = [
    "AppointmentCancelled",
    "AppointmentCompleted",
    "AppointmentConfirmed",
    "AppointmentOut",
    "AppointmentRescheduled",
    "BookingRequest",
    "HoldDto",
    "SlotOut",
    "SlotQuery",
    "agenda",
    "bind_hold",
    "book",
    "completed_service_page",
    "customer_ids_with_service",
    "find_slots",
    "get_appointment",
    "has_future_service_appointment",
    "latest_completed_appointment",
    "list_appointments",
    "place_hold",
]


def get_appointment(db: Session, ctx: Context, appointment_id: uuid.UUID, *, lock: bool = True) -> AppointmentOut:
    """Read a scoped appointment, including customer, service and completion status (sales invoicing)."""
    from app.platform.crud import require_scoped

    from .models import Appointment

    row = require_scoped(db, ctx, Appointment, appointment_id, "scheduling.appointment_not_found", for_update=lock)
    return AppointmentOut(**service.enrich(db, ctx, [row])[0])
