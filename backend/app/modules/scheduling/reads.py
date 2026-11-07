"""Scheduling rules (M06). The database exclusion constraint is the final guard against double booking."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, text, update
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.crm import public as crm
from app.platform import clock, jobs
from app.platform.context import Context
from app.platform.errors import Invalid, NotFound

from . import permissions as P
from .models import Appointment, AppointmentResource
from .resources import list_resources


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
        stmt = stmt.where(
            or_(
                Appointment.resource_id == resource_id,
                Appointment.id.in_(
                    select(AppointmentResource.appointment_id).where(
                        AppointmentResource.business_id == ctx.business_id,
                        AppointmentResource.resource_id == resource_id,
                    )
                ),
            )
        )
    if customer_id:
        stmt = stmt.where(Appointment.customer_id == customer_id)
    stmt = stmt.where(Appointment.status == status) if status else stmt.where(Appointment.status != "cancelled")
    return list(db.scalars(stmt.order_by(Appointment.starts_at)))


def enrich(db: Session, ctx: Context, rows: list[Appointment]) -> list[dict[str, Any]]:
    customers = crm.customer_names(db, ctx, {a.customer_id for a in rows})
    services = catalog.get_items(db, ctx, {a.service_id for a in rows})
    resources = {r.id: r.name for r in list_resources(db, ctx, include_archived=True)}
    from .schemas import AllocatedResource, AppointmentOut

    allocations = list(
        db.scalars(
            select(AppointmentResource).where(
                AppointmentResource.business_id == ctx.business_id,
                AppointmentResource.appointment_id.in_([a.id for a in rows]),
            )
        )
    )
    return [
        AppointmentOut.model_validate(a)
        .model_copy(
            update={
                "resources": [
                    AllocatedResource(
                        role=(
                            services[a.service_id].required_resources[0].role
                            if r.resource_id == a.resource_id
                            else r.role
                        ),
                        resource_id=r.resource_id,
                        resource_name=resources.get(r.resource_id, ""),
                    )
                    for r in sorted(allocations, key=lambda r: (r.resource_id != a.resource_id, r.role))
                    if r.appointment_id == a.id
                ]
                if services[a.service_id].required_resources
                else [],
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
