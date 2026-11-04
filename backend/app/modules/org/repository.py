"""All SQL for the org module. Every query is business-scoped (row-level security also applies)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.platform.context import Context

from .models import Business, BusinessSettingsRow, Location, OpeningHours


def get_business(db: Session, ctx: Context) -> Business | None:
    return db.scalar(select(Business).where(Business.id == ctx.business_id))


def update_business(db: Session, ctx: Context, expected_version: int, changes: dict[str, Any]) -> Business | None:
    stmt = (
        update(Business)
        .where(Business.id == ctx.business_id, Business.version == expected_version)
        .values(**changes, version=Business.version + 1, updated_at=func.now())
        .returning(Business)
    )
    return db.scalar(stmt)


def list_locations(db: Session, ctx: Context) -> list[Location]:
    stmt = (
        select(Location)
        .where(Location.business_id == ctx.business_id)
        .order_by(Location.is_default.desc(), Location.name)
    )
    return list(db.scalars(stmt))


def get_location(db: Session, ctx: Context, location_id: uuid.UUID) -> Location | None:
    return db.scalar(select(Location).where(Location.business_id == ctx.business_id, Location.id == location_id))


def get_default_location(db: Session, ctx: Context) -> Location | None:
    return db.scalar(select(Location).where(Location.business_id == ctx.business_id, Location.is_default.is_(True)))


def clear_default_location(db: Session, ctx: Context) -> None:
    db.execute(
        update(Location)
        .where(Location.business_id == ctx.business_id, Location.is_default.is_(True))
        .values(is_default=False, version=Location.version + 1)
    )


def update_location(
    db: Session, ctx: Context, location_id: uuid.UUID, expected_version: int, changes: dict[str, Any]
) -> Location | None:
    stmt = (
        update(Location)
        .where(
            Location.business_id == ctx.business_id, Location.id == location_id, Location.version == expected_version
        )
        .values(**changes, version=Location.version + 1, updated_at=func.now())
        .returning(Location)
    )
    return db.scalar(stmt)


def get_opening_hours(db: Session, ctx: Context, location_id: uuid.UUID) -> list[OpeningHours]:
    stmt = (
        select(OpeningHours)
        .where(OpeningHours.business_id == ctx.business_id, OpeningHours.location_id == location_id)
        .order_by(OpeningHours.weekday, OpeningHours.opens)
    )
    return list(db.scalars(stmt))


def replace_opening_hours(db: Session, ctx: Context, location_id: uuid.UUID, rows: list[OpeningHours]) -> None:
    db.execute(
        delete(OpeningHours).where(OpeningHours.business_id == ctx.business_id, OpeningHours.location_id == location_id)
    )
    db.add_all(rows)
    db.flush()


def get_settings_row(db: Session, ctx: Context) -> BusinessSettingsRow | None:
    return db.scalar(select(BusinessSettingsRow).where(BusinessSettingsRow.business_id == ctx.business_id))


def update_settings_row(
    db: Session, ctx: Context, expected_version: int, settings: dict[str, Any], *, sales_tax_rate: Decimal
) -> BusinessSettingsRow | None:
    stmt = (
        update(BusinessSettingsRow)
        .where(BusinessSettingsRow.business_id == ctx.business_id, BusinessSettingsRow.version == expected_version)
        .values(
            settings=settings,
            sales_tax_rate=sales_tax_rate,
            version=BusinessSettingsRow.version + 1,
            updated_at=func.now(),
        )
        .returning(BusinessSettingsRow)
    )
    return db.scalar(stmt)
