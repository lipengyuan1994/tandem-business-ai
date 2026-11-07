"""Scheduling rules (M06). The database exclusion constraint is the final guard against double booking."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.org import public as org
from app.platform import audit, crud
from app.platform.context import Context
from app.platform.errors import Invalid
from app.platform.ids import new_id

from . import permissions as P
from .models import AvailabilityException, AvailabilityRule, Resource, ResourceService
from .schemas import ExceptionIn, ResourceIn, ResourceUpdate, RuleIn


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


# ------------------------------------------------------------------------------------------- holds and bookings
