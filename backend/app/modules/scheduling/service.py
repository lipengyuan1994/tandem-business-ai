"""Scheduling rules (M06). The database exclusion constraint is the final guard against double booking."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.errors import DeadlockDetected, ExclusionViolation
from pydantic import Field
from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.catalog import public as catalog
from app.modules.crm import public as crm
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.platform import audit, clock, crud, events, jobs
from app.platform.context import Context
from app.platform.errors import Conflict, Forbidden, Invalid, NotFound
from app.platform.events import DomainEventPayload
from app.platform.ids import new_id

from . import availability as av
from . import permissions as P
from .models import Appointment, AvailabilityException, AvailabilityRule, Resource, ResourceService
from .schemas import BookingRequest, ExceptionIn, ResourceIn, ResourceUpdate, RuleIn, SlotOut, SlotQuery

MAX_SPAN = timedelta(days=14)
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


# --------------------------------------------------------------------------------------------------- resources
def create_resource(db: Session, ctx: Context, data: ResourceIn) -> Resource:
    ctx.require(P.RESOURCE_WRITE)
    assert ctx.business_id is not None
    location_id = data.location_id or org.get_default_location(db, ctx).id
    r = Resource(
        id=new_id(),
        business_id=ctx.business_id,
        location_id=location_id,
        kind=data.kind,
        name=data.name,
        user_id=data.user_id,
        color=data.color,
        capacity=1,
        status="active",
    )
    db.add(r)
    db.flush()
    audit.record(
        db,
        ctx,
        action="scheduling.resource.created",
        target_type="resource",
        target_id=r.id,
        summary=f"Resource '{r.name}' created",
    )
    return r


def update_resource(db: Session, ctx: Context, resource_id: uuid.UUID, data: ResourceUpdate) -> Resource:
    ctx.require(P.RESOURCE_WRITE)
    r = crud.update_versioned(
        db,
        ctx,
        Resource,
        resource_id,
        data.version,
        data.model_dump(exclude_unset=True),
        "scheduling.resource_version_conflict",
    )
    audit.record(
        db,
        ctx,
        action="scheduling.resource.updated",
        target_type="resource",
        target_id=r.id,
        summary=f"Resource '{r.name}' updated",
    )
    return r


def list_resources(db: Session, ctx: Context, include_archived: bool = False) -> list[Resource]:
    stmt = select(Resource).where(Resource.business_id == ctx.business_id)
    if not include_archived:
        stmt = stmt.where(Resource.status == "active")
    return list(db.scalars(stmt.order_by(Resource.name)))


def _resource(db: Session, ctx: Context, resource_id: uuid.UUID) -> Resource:
    return crud.require_scoped(db, ctx, Resource, resource_id, "scheduling.resource_not_found")


def replace_rules(db: Session, ctx: Context, resource_id: uuid.UUID, rules: list[RuleIn]) -> list[AvailabilityRule]:
    ctx.require(P.RESOURCE_WRITE)
    _resource(db, ctx, resource_id)
    for r in rules:
        if r.end_time <= r.start_time:
            raise Invalid("scheduling.invalid_rule", detail=f"weekday {r.weekday}: end must be after start")
    db.execute(
        delete(AvailabilityRule).where(
            AvailabilityRule.business_id == ctx.business_id, AvailabilityRule.resource_id == resource_id
        )
    )
    assert ctx.business_id is not None
    db.add_all(AvailabilityRule(business_id=ctx.business_id, resource_id=resource_id, **r.model_dump()) for r in rules)
    db.flush()
    audit.record(
        db,
        ctx,
        action="scheduling.availability.replaced",
        target_type="resource",
        target_id=resource_id,
        summary=f"{len(rules)} weekly rule(s)",
    )
    return rules_of(db, ctx, resource_id)


def rules_of(db: Session, ctx: Context, resource_id: uuid.UUID) -> list[AvailabilityRule]:
    return list(
        db.scalars(
            select(AvailabilityRule)
            .where(AvailabilityRule.business_id == ctx.business_id, AvailabilityRule.resource_id == resource_id)
            .order_by(AvailabilityRule.weekday, AvailabilityRule.start_time)
        )
    )


def replace_services(
    db: Session, ctx: Context, resource_id: uuid.UUID, service_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    ctx.require(P.RESOURCE_WRITE)
    _resource(db, ctx, resource_id)
    for sid in service_ids:
        catalog.get_service_requirements(db, ctx, sid)
    db.execute(
        delete(ResourceService).where(
            ResourceService.business_id == ctx.business_id, ResourceService.resource_id == resource_id
        )
    )
    assert ctx.business_id is not None
    db.add_all(
        ResourceService(business_id=ctx.business_id, resource_id=resource_id, service_id=s)
        for s in dict.fromkeys(service_ids)
    )
    db.flush()
    return services_of(db, ctx, resource_id)


def services_of(db: Session, ctx: Context, resource_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(ResourceService.service_id).where(
                ResourceService.business_id == ctx.business_id, ResourceService.resource_id == resource_id
            )
        )
    )


def add_exception(db: Session, ctx: Context, data: ExceptionIn) -> AvailabilityException:
    ctx.require(P.RESOURCE_WRITE)
    if data.ends_at <= data.starts_at:
        raise Invalid("scheduling.invalid_window")
    location_id = org.get_default_location(db, ctx).id
    if data.resource_id:
        location_id = _resource(db, ctx, data.resource_id).location_id
    assert ctx.business_id is not None
    row = AvailabilityException(business_id=ctx.business_id, location_id=location_id, **data.model_dump())
    db.add(row)
    db.flush()
    audit.record(
        db,
        ctx,
        action="scheduling.exception.added",
        target_type="resource",
        target_id=data.resource_id,
        summary=f"{data.kind} {data.reason or ''}".strip(),
    )
    return row


# ------------------------------------------------------------------------------------------------- slot finder
def _qualified(
    db: Session, ctx: Context, service_id: uuid.UUID, resource_kind: str, location_id: uuid.UUID
) -> list[Resource]:
    candidates = [r for r in list_resources(db, ctx) if r.kind == resource_kind and r.location_id == location_id]
    linked = {
        r
        for r, s in db.execute(
            select(ResourceService.resource_id, ResourceService.service_id).where(
                ResourceService.business_id == ctx.business_id
            )
        )
    }
    explicit = {
        r
        for r, s in db.execute(
            select(ResourceService.resource_id, ResourceService.service_id).where(
                ResourceService.business_id == ctx.business_id, ResourceService.service_id == service_id
            )
        )
    }
    if not explicit:  # no explicit qualification for this service: every resource of the kind qualifies
        return candidates
    return [r for r in candidates if r.id in explicit or r.id not in linked]


def _busy(
    db: Session,
    ctx: Context,
    resource_ids: list[uuid.UUID],
    start: datetime,
    end: datetime,
    ignore: uuid.UUID | None = None,
) -> dict[uuid.UUID, list[av.Interval]]:
    now = clock.now()
    stmt = select(Appointment.resource_id, Appointment.starts_at, Appointment.blocked_until).where(
        Appointment.business_id == ctx.business_id,
        Appointment.resource_id.in_(resource_ids),
        Appointment.starts_at < end,
        Appointment.blocked_until > start,
        or_(Appointment.status == "confirmed", and_(Appointment.status == "held", Appointment.hold_expires_at > now)),
    )
    if ignore:
        stmt = stmt.where(Appointment.id != ignore)
    out: dict[uuid.UUID, list[av.Interval]] = {}
    for rid, s, b in db.execute(stmt):
        out.setdefault(rid, []).append((s, b))
    return out


def find_slots(
    db: Session,
    ctx: Context,
    q: SlotQuery,
    *,
    ignore_appointment: uuid.UUID | None = None,
    ignore_lead_time: bool = False,
) -> list[SlotOut]:
    if q.latest <= q.earliest or q.latest - q.earliest > MAX_SPAN:
        raise Invalid("scheduling.invalid_window", detail="window must be positive and at most 14 days")
    req = catalog.get_service_requirements(db, ctx, q.service_id)
    settings = org.get_settings(db, ctx)
    location = org.get_default_location(db, ctx)
    if q.location_id and q.location_id != location.id:
        raise Invalid("scheduling.invalid_window", detail="only the default location is supported in MVP")
    tz = ZoneInfo(location.timezone)
    resources = _qualified(db, ctx, q.service_id, req.resource_kind, location.id)
    if q.resource_id:
        resources = [r for r in resources if r.id == q.resource_id]
    if not resources:
        return []
    hours = org.get_opening_hours(db, ctx, location.id)
    duration = timedelta(minutes=req.duration_minutes)
    block = duration + timedelta(minutes=req.buffer_minutes)
    not_before = (
        q.earliest
        if ignore_lead_time
        else max(q.earliest, clock.now() + timedelta(minutes=settings.booking_lead_time_minutes))
    )
    busy = _busy(db, ctx, [r.id for r in resources], q.earliest - block, q.latest + block, ignore_appointment)
    exceptions = list(
        db.scalars(
            select(AvailabilityException).where(
                AvailabilityException.business_id == ctx.business_id,
                AvailabilityException.location_id == location.id,
                AvailabilityException.starts_at < q.latest + timedelta(days=1),
                AvailabilityException.ends_at > q.earliest - timedelta(days=1),
            )
        )
    )
    slots: list[SlotOut] = []
    first_day, last_day = q.earliest.astimezone(tz).date(), q.latest.astimezone(tz).date()
    for r in resources:
        rules = rules_of(db, ctx, r.id)
        closed = [(e.starts_at, e.ends_at) for e in exceptions if e.kind == "closed" and e.resource_id in (None, r.id)]
        extra = [(e.starts_at, e.ends_at) for e in exceptions if e.kind == "extra_open" and e.resource_id == r.id]
        day = first_day
        while day <= last_day:
            day_rules = av.DayRules(
                open_hours=[(h.opens, h.closes) for h in hours if h.weekday == day.weekday()],
                resource_hours=[
                    (x.start_time, x.end_time)
                    for x in rules
                    if x.weekday == day.weekday()
                    and (x.valid_from is None or x.valid_from <= day)
                    and (x.valid_to is None or x.valid_to >= day)
                ],
            )
            free = av.subtract(av.working_intervals(day, tz, day_rules, closed, extra), busy.get(r.id, []))
            for start in av.slot_starts(
                free,
                tz=tz,
                granularity_min=settings.slot_granularity_minutes,
                block=block,
                not_before=not_before,
                latest_start=q.latest,
            ):
                slots.append(
                    SlotOut(
                        starts_at=start,
                        ends_at=start + duration,
                        resource_id=r.id,
                        resource_name=r.name,
                        local_label=av.local_label(start, tz),
                    )
                )
            day += timedelta(days=1)
    slots.sort(key=lambda s: (s.starts_at, s.resource_name))
    return slots[: q.limit]


def _offered(
    db: Session,
    ctx: Context,
    service_id: uuid.UUID,
    resource_id: uuid.UUID,
    starts_at: datetime,
    ignore: uuid.UUID | None = None,
) -> bool:
    q = SlotQuery(
        service_id=service_id,
        earliest=starts_at,
        latest=starts_at + timedelta(days=1),
        resource_id=resource_id,
        limit=1,
    )
    found = find_slots(db, ctx, q, ignore_appointment=ignore)
    return bool(found and found[0].starts_at == starts_at)


# ------------------------------------------------------------------------------------------- holds and bookings
def _insert_guarded(db: Session, row: Appointment) -> None:
    """Insert or flush inside a savepoint; an overlap becomes a clean 409 instead of a broken transaction."""
    try:
        with db.begin_nested():
            db.add(row)
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
            Appointment.resource_id == resource_id,
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
    if not _offered(db, ctx, service_id, resource_id, starts_at):
        raise Conflict("scheduling.slot_unavailable")
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
        timezone=org.get_default_location(db, ctx).timezone,
        status="held",
        hold_expires_at=clock.now() + timedelta(minutes=settings.hold_minutes),
        proposal_id=proposal_id,
        source="assistant" if ctx.actor_kind == "assistant" else "staff",
    )
    _insert_guarded(db, hold)
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
    if (
        hold is None
        and not req.override_hours
        and not _offered(db, ctx, req.service_id, req.resource_id, req.starts_at)
    ):
        raise Conflict("scheduling.slot_unavailable")
    _release_expired_overlapping(db, ctx, req.resource_id, req.starts_at, blocked)
    if hold is not None:
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
        except IntegrityError as exc:
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
            timezone=org.get_default_location(db, ctx).timezone,
            status="confirmed",
            proposal_id=ctx.proposal_id,
            source="assistant" if ctx.proposal_id else "staff",
            notes=req.notes,
        )
        _insert_guarded(db, appointment)
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
    if not override_hours and not _offered(db, ctx, a.service_id, target_resource, new_start, ignore=a.id):
        raise Conflict("scheduling.slot_unavailable")
    shift = new_start - a.starts_at
    # Expired holds are only cancelled by a once-a-minute job; free the target interval like booking does.
    _release_expired_overlapping(db, ctx, target_resource, new_start, a.blocked_until + shift)
    old_label = av.local_label(a.starts_at, ZoneInfo(a.timezone))
    try:
        with db.begin_nested():
            a.starts_at, a.ends_at, a.blocked_until = new_start, a.ends_at + shift, a.blocked_until + shift
            a.resource_id, a.version = target_resource, a.version + 1
            db.flush()
    except IntegrityError as exc:
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


# --------------------------------------------------------------------------------------- reads, jobs, handlers
def customer_ids_with_service(db: Session, ctx: Context, service_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(Appointment.customer_id).where(
                Appointment.business_id == ctx.business_id,
                Appointment.service_id == service_id,
                Appointment.status.in_(("confirmed", "completed")),
            )
        )
    )


def list_appointments(
    db: Session,
    ctx: Context,
    *,
    start: datetime,
    end: datetime,
    resource_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[Appointment]:
    ctx.require(P.APPOINTMENT_READ)
    if end <= start or end - start > timedelta(days=62):
        raise Invalid("scheduling.invalid_window", detail="window must be positive and at most 62 days")
    stmt = select(Appointment).where(
        Appointment.business_id == ctx.business_id, Appointment.starts_at < end, Appointment.ends_at > start
    )
    if resource_id:
        stmt = stmt.where(Appointment.resource_id == resource_id)
    if customer_id:
        stmt = stmt.where(Appointment.customer_id == customer_id)
    stmt = stmt.where(Appointment.status == status) if status else stmt.where(Appointment.status != "cancelled")
    return list(db.scalars(stmt.order_by(Appointment.starts_at)))


def enrich(db: Session, ctx: Context, rows: list[Appointment]) -> list[dict[str, Any]]:
    customers = crm.customer_names(db, ctx, {a.customer_id for a in rows})
    services = catalog.get_items(db, ctx, {a.service_id for a in rows})
    resources = {r.id: r.name for r in list_resources(db, ctx, include_archived=True)}
    from .schemas import AppointmentOut

    return [
        AppointmentOut.model_validate(a)
        .model_copy(
            update={
                "customer_name": customers.get(a.customer_id, ""),
                "service_name": services[a.service_id].name if a.service_id in services else "",
                "resource_name": resources.get(a.resource_id, ""),
            }
        )
        .model_dump()
        for a in rows
    ]


def job_release_holds_scan(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    for business_id in db.execute(
        text("select * from businesses_with_expired_holds(:now)"), {"now": clock.now()}
    ).scalars():
        jobs.enqueue(
            db,
            "scheduling.release_expired_holds",
            {},
            business_id=business_id,
            dedupe_key=f"holds:{business_id}:{int(clock.now().timestamp()) // 60}",
        )


def job_release_expired_holds(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    db.execute(
        update(Appointment)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.status == "held",
            Appointment.hold_expires_at <= clock.now(),
        )
        .values(status="cancelled", cancelled_reason="hold expired")
    )


def on_proposal_closed(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    """A proposal that did not succeed releases its holds at once (idempotent)."""
    if payload.get("outcome") == "succeeded":
        return
    db.execute(
        update(Appointment)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.status == "held",
            Appointment.proposal_id == uuid.UUID(payload["proposal_id"]),
        )
        .values(status="cancelled", cancelled_reason=f"proposal {payload.get('outcome')}")
    )


def agenda_for_day(db: Session, ctx: Context, start: datetime, end: datetime) -> list[Appointment]:
    stmt = select(Appointment).where(
        Appointment.business_id == ctx.business_id,
        Appointment.starts_at < end,
        Appointment.ends_at > start,
        Appointment.status.in_(("confirmed", "held", "completed")),
    )
    return list(db.scalars(stmt.order_by(Appointment.starts_at)))


def ensure_found(row: object | None, code: str) -> None:
    if row is None:
        raise NotFound(code)
