"""Scheduling rules (M06). The database exclusion constraint is the final guard against double booking."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.errors import DeadlockDetected, ExclusionViolation
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.catalog import public as catalog
from app.modules.crm import public as crm
from app.modules.org import public as org
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.errors import Conflict
from app.platform.ids import new_id

from . import availability as av
from . import permissions as P
from .models import Appointment, AppointmentResource
from .resources import _resource
from .schemas import SlotOut
from .slots import _offered as _offered

ACTIVE = ("held", "confirmed")


def _allocate(db: Session, row: Appointment, slot: SlotOut | None) -> None:
    if slot:
        for resource in sorted(slot.resources, key=lambda r: r.resource_id):
            if resource.resource_id != row.resource_id:
                db.add(
                    AppointmentResource(
                        business_id=row.business_id,
                        appointment_id=row.id,
                        resource_id=resource.resource_id,
                        role=resource.role,
                        starts_at=row.starts_at,
                        blocked_until=row.blocked_until,
                        active=row.status in ACTIVE,
                    )
                )


def _allocation(
    db: Session,
    ctx: Context,
    service_id: uuid.UUID,
    resource_id: uuid.UUID,
    starts_at: datetime,
    *,
    ignore: uuid.UUID | None = None,
    override: bool = False,
    ignore_lead_time: bool = False,
) -> SlotOut | None:
    slot = _offered(db, ctx, service_id, resource_id, starts_at, ignore, ignore_lead_time=ignore_lead_time)
    required = catalog.get_service_requirements(db, ctx, service_id).required_resources
    # An hours override never bypasses qualification, location, or multi-resource availability.
    if not slot and (not override or required):
        raise Conflict("scheduling.slot_unavailable")
    return slot


def _insert_guarded(db: Session, row: Appointment, slot: SlotOut | None = None) -> None:
    """Insert or flush inside a savepoint; an overlap becomes a clean 409 instead of a broken transaction."""
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
            _allocate(db, row, slot)
            db.flush()
    except IntegrityError as exc:
        if isinstance(exc.orig, ExclusionViolation):
            raise Conflict("scheduling.slot_unavailable") from exc
        raise
    except DBAPIError as exc:
        # Two overlapping inserts can wait on each other's exclusion check; PostgreSQL aborts one. The slot is taken.
        if isinstance(exc.orig, DeadlockDetected):
            raise Conflict("scheduling.slot_unavailable") from exc
        raise


def _release_expired_overlapping(
    db: Session, ctx: Context, resource_id: uuid.UUID, start: datetime, end: datetime
) -> None:
    db.execute(
        update(Appointment)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.status == "held",
            Appointment.hold_expires_at <= clock.now(),
            Appointment.starts_at < end,
            Appointment.blocked_until > start,
        )
        .values(status="cancelled", cancelled_reason="hold expired")
    )


def place_hold(
    db: Session,
    ctx: Context,
    *,
    service_id: uuid.UUID,
    resource_id: uuid.UUID,
    starts_at: datetime,
    customer_id: uuid.UUID,
    proposal_id: uuid.UUID | None = None,
) -> Appointment:
    """Block a slot while a proposal is reviewed. Reversible; never notifies anyone and never reserves stock."""
    ctx.require(P.APPOINTMENT_WRITE)
    req = catalog.get_service_requirements(db, ctx, service_id)
    resource = _resource(db, ctx, resource_id)
    crm.get_booking_customer(db, ctx, customer_id)
    slot = _allocation(db, ctx, service_id, resource_id, starts_at)
    settings = org.get_settings(db, ctx)
    ends = starts_at + timedelta(minutes=req.duration_minutes)
    blocked = ends + timedelta(minutes=req.buffer_minutes)
    _release_expired_overlapping(db, ctx, resource_id, starts_at, blocked)
    assert ctx.business_id is not None
    hold = Appointment(
        id=new_id(),
        business_id=ctx.business_id,
        customer_id=customer_id,
        service_id=service_id,
        resource_id=resource_id,
        location_id=resource.location_id,
        starts_at=starts_at,
        ends_at=ends,
        blocked_until=blocked,
        timezone=org.get_location(db, ctx, resource.location_id).timezone,
        status="held",
        hold_expires_at=clock.now() + timedelta(minutes=settings.hold_minutes),
        proposal_id=proposal_id,
        source="assistant" if ctx.actor_kind == "assistant" else "staff",
    )
    _insert_guarded(db, hold, slot)
    audit.record(
        db,
        ctx,
        action="scheduling.hold.placed",
        target_type="appointment",
        target_id=hold.id,
        summary=f"Held {av.local_label(starts_at, ZoneInfo(hold.timezone))} for {settings.hold_minutes} min",
        proposal_id=proposal_id,
    )
    return hold


def bind_hold(db: Session, ctx: Context, hold_id: uuid.UUID, proposal_id: uuid.UUID) -> None:
    """Bind a held appointment in this business once; repeated binding to the same proposal is safe."""
    ctx.require(P.APPOINTMENT_WRITE)
    hold = db.scalar(
        select(Appointment)
        .where(Appointment.business_id == ctx.business_id, Appointment.id == hold_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if hold is None or hold.status != "held" or hold.proposal_id not in (None, proposal_id):
        raise Conflict("scheduling.hold_not_bindable")
    if not approvals.proposal_exists(db, ctx, proposal_id):
        raise Conflict("scheduling.hold_not_bindable")
    if hold.proposal_id == proposal_id:
        return
    hold.proposal_id = proposal_id
    db.flush()
    audit.record(
        db,
        ctx,
        action="scheduling.hold.bound",
        target_type="appointment",
        target_id=hold.id,
        summary="Bound hold to proposal",
        proposal_id=proposal_id,
    )
