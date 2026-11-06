"""Scheduling: export, appointment notes erasure, and the rule that upcoming appointments block an erasure (M18)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import Appointment, AvailabilityException, AvailabilityRule, Resource, ResourceService
from .self_service_models import SelfServiceChange, SelfServiceSession, SelfServiceToken


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(db, ctx, [Resource, ResourceService, AvailabilityRule, AvailabilityException, Appointment])
    tables.update(
        stream_models(db, ctx, [SelfServiceChange, SelfServiceSession, SelfServiceToken], exclude={"token_hash"})
    )
    return export_tables(tables, streaming=streaming)


def links(db: Session, ctx: Context, target: ErasureTarget) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(Appointment.id).where(
                Appointment.business_id == ctx.business_id, Appointment.customer_id == target.customer_id
            )
        )
    )


def check(db: Session, ctx: Context, target: ErasureTarget) -> list[str]:
    upcoming = db.scalar(
        select(func.count())
        .select_from(Appointment)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.customer_id == target.customer_id,
            Appointment.status.in_(("held", "confirmed")),
            Appointment.ends_at > clock.now(),
        )
    )
    return [f"{upcoming} upcoming appointment(s): cancel or complete them first"] if upcoming else []


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Appointments stay (times, services, resources); notes and cancellation reasons about the person are removed."""
    from .self_service import revoke

    revoke(db, ctx, target.customer_id)
    db.execute(
        update(SelfServiceChange)
        .where(SelfServiceChange.business_id == ctx.business_id, SelfServiceChange.customer_id == target.customer_id)
        .values(fee_text=None)
    )
    result = db.execute(
        update(Appointment)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.customer_id == target.customer_id,
            or_(Appointment.notes.is_not(None), Appointment.cancelled_reason.is_not(None)),
        )
        .values(notes=None, cancelled_reason=None)
    )
    return {"appointment_texts_removed": rows_affected(result)}
