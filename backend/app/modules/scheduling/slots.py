"""Deterministic location-scoped single and multi-resource slot allocation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.org import public as org
from app.platform import clock
from app.platform.context import Context
from app.platform.errors import Invalid

from . import availability as av
from .models import Appointment, AppointmentResource, AvailabilityException, Resource, ResourceService
from .schemas import AllocatedResource, SlotOut, SlotQuery

MAX_SPAN = timedelta(days=14)


# ------------------------------------------------------------------------------------------------- slot finder
def _qualified(
    db: Session, ctx: Context, service_id: uuid.UUID, resource_kind: str, location_id: uuid.UUID
) -> list[Resource]:
    from .resources import list_resources

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
    requirements = catalog.get_service_requirements(db, ctx, service_id)
    explicit_kind = {r.id for r in list_resources(db, ctx) if r.kind == resource_kind and r.id in explicit}
    if requirements.required_resources:
        return [r for r in candidates if r.id in explicit_kind] if explicit_kind else candidates
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
    secondary = (
        select(AppointmentResource.resource_id, AppointmentResource.starts_at, AppointmentResource.blocked_until)
        .join(
            Appointment,
            (Appointment.id == AppointmentResource.appointment_id)
            & (Appointment.business_id == AppointmentResource.business_id),
        )
        .where(
            AppointmentResource.business_id == ctx.business_id,
            AppointmentResource.resource_id.in_(resource_ids),
            AppointmentResource.starts_at < end,
            AppointmentResource.blocked_until > start,
            or_(
                Appointment.status == "confirmed", and_(Appointment.status == "held", Appointment.hold_expires_at > now)
            ),
        )
    )
    if ignore:
        secondary = secondary.where(Appointment.id != ignore)
    for rid, s, b in db.execute(secondary):
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
    if q.location_id:
        location = org.get_location(db, ctx, q.location_id)
    tz = ZoneInfo(location.timezone)
    resources = _qualified(db, ctx, q.service_id, req.resource_kind, location.id)
    if q.resource_id:
        resources = [r for r in resources if r.id == q.resource_id]
    if not resources:
        return []
    groups = [(x.role, _qualified(db, ctx, q.service_id, x.kind, location.id)) for x in req.required_resources[1:]]
    if any(not candidates for _, candidates in groups):
        return []
    all_resources = {r.id: r for r in resources + [r for _, group in groups for r in group]}
    hours = org.get_opening_hours(db, ctx, location.id)
    duration = timedelta(minutes=req.duration_minutes)
    block = duration + timedelta(minutes=req.buffer_minutes)
    not_before = (
        q.earliest
        if ignore_lead_time
        else max(q.earliest, clock.now() + timedelta(minutes=settings.booking_lead_time_minutes))
    )
    busy = _busy(db, ctx, list(all_resources), q.earliest - block, q.latest + block, ignore_appointment)
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
    from .resources import rules_of

    free_by_resource: dict[uuid.UUID, list[av.Interval]] = {}
    for r in all_resources.values():
        rules = rules_of(db, ctx, r.id)
        free_by_resource[r.id] = []
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
            free_by_resource[r.id].extend(free)
            day += timedelta(days=1)
    for r in resources:
        for start in av.slot_starts(
            free_by_resource[r.id],
            tz=tz,
            granularity_min=settings.slot_granularity_minutes,
            block=block,
            not_before=not_before,
            latest_start=q.latest,
        ):
            allocation = [
                AllocatedResource(
                    role=req.required_resources[0].role if req.required_resources else "primary",
                    resource_id=r.id,
                    resource_name=r.name,
                )
            ]
            for role, candidates in groups:
                candidate = next(
                    (
                        c
                        for c in candidates
                        if any(s <= start and start + block <= e for s, e in free_by_resource[c.id])
                    ),
                    None,
                )
                if candidate is None:
                    break
                allocation.append(AllocatedResource(role=role, resource_id=candidate.id, resource_name=candidate.name))
            else:
                slots.append(
                    SlotOut(
                        starts_at=start,
                        ends_at=start + duration,
                        resource_id=r.id,
                        resource_name=r.name,
                        local_label=av.local_label(start, tz),
                        resources=allocation if req.required_resources else [],
                    )
                )
    slots.sort(key=lambda s: (s.starts_at, s.resource_name))
    return slots[: q.limit]


def _offered(
    db: Session,
    ctx: Context,
    service_id: uuid.UUID,
    resource_id: uuid.UUID,
    starts_at: datetime,
    ignore: uuid.UUID | None = None,
    *,
    ignore_lead_time: bool = False,
) -> SlotOut | None:
    resource = db.scalar(select(Resource).where(Resource.business_id == ctx.business_id, Resource.id == resource_id))
    q = SlotQuery(
        location_id=resource.location_id if resource else None,
        service_id=service_id,
        earliest=starts_at,
        latest=starts_at + timedelta(days=1),
        resource_id=resource_id,
        limit=1,
    )
    found = find_slots(db, ctx, q, ignore_appointment=ignore, ignore_lead_time=ignore_lead_time)
    return found[0] if found and found[0].starts_at == starts_at else None
