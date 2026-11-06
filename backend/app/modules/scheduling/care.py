"""Bounded completed-service discovery for post-sales care; no message generation."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.context import Context

from . import service
from .models import Appointment
from .schemas import AppointmentOut


def completed_page(
    db: Session, ctx: Context, service_id: uuid.UUID, *, after: uuid.UUID | None = None, limit: int = 500
) -> list[AppointmentOut]:
    ctx.require("scheduling.appointment.read")
    latest = (
        select(Appointment.id, Appointment.customer_id)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.service_id == service_id,
            Appointment.status == "completed",
            Appointment.ends_at <= clock.now(),
        )
        .ext(distinct_on(Appointment.customer_id))
        .order_by(Appointment.customer_id, Appointment.ends_at.desc(), Appointment.id.desc())
        .subquery()
    )
    stmt = (
        select(Appointment)
        .join(latest, latest.c.id == Appointment.id)
        .where(Appointment.business_id == ctx.business_id)
    )
    if after:
        stmt = stmt.where(Appointment.customer_id > after)
    rows = list(db.scalars(stmt.order_by(Appointment.customer_id).limit(min(limit, 500))))
    return [AppointmentOut(**r) for r in service.enrich(db, ctx, rows)]


def has_future(db: Session, ctx: Context, customer_id: uuid.UUID, service_id: uuid.UUID) -> bool:
    ctx.require("scheduling.appointment.read")
    return (
        db.scalar(
            select(Appointment.id)
            .where(
                Appointment.business_id == ctx.business_id,
                Appointment.customer_id == customer_id,
                Appointment.service_id == service_id,
                Appointment.starts_at > clock.now(),
                (Appointment.status == "confirmed")
                | ((Appointment.status == "held") & (Appointment.hold_expires_at > clock.now())),
            )
            .limit(1)
        )
        is not None
    )


def latest_completed(
    db: Session, ctx: Context, customer_id: uuid.UUID, service_id: uuid.UUID | None = None
) -> AppointmentOut | None:
    ctx.require("scheduling.appointment.read")
    stmt = select(Appointment).where(
        Appointment.business_id == ctx.business_id,
        Appointment.customer_id == customer_id,
        Appointment.status == "completed",
        Appointment.ends_at <= clock.now(),
    )
    if service_id:
        stmt = stmt.where(Appointment.service_id == service_id)
    row = db.scalar(stmt.order_by(Appointment.ends_at.desc(), Appointment.id.desc()).limit(1))
    return AppointmentOut(**service.enrich(db, ctx, [row])[0]) if row else None
