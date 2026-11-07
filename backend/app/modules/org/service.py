"""Business rules for the org module. Services never commit; endpoints own the transaction."""

from __future__ import annotations

import itertools
import re
import uuid
from datetime import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform import audit, events
from app.platform.context import Context
from app.platform.errors import Conflict, Forbidden, Invalid, NotFound
from app.platform.ids import new_id

from . import permissions as P
from . import repository
from .events import OpeningHoursChanged, SettingsChanged
from .models import Business, BusinessSettingsRow, Location, OpeningHours
from .schemas import (
    BusinessBrief,
    BusinessSettings,
    BusinessUpdate,
    LocationCreate,
    LocationUpdate,
    OpeningHoursItem,
    validate_timezone,
)

SLUG_RE = re.compile(r"^[a-z0-9-]{3,40}$")
BUSINESS_FIELDS = ["name", "legal_name", "timezone", "locale", "country", "public_profile"]


def _business(db: Session, ctx: Context) -> Business:
    business = repository.get_business(db, ctx)
    if business is None:
        raise NotFound("org.business_not_found")
    return business


def brief(business: Business) -> BusinessBrief:
    return BusinessBrief(
        id=business.id,
        slug=business.slug,
        name=business.name,
        timezone=business.timezone,
        currency=business.currency,
        locale=business.locale,
    )


# ------------------------------------------------------------------------------------------------ business
def get_business(db: Session, ctx: Context) -> Business:
    ctx.require(P.BUSINESS_READ)
    return _business(db, ctx)


def update_business(db: Session, ctx: Context, data: BusinessUpdate) -> Business:
    ctx.require(P.BUSINESS_WRITE)
    before = _business(db, ctx)
    snapshot = {f: getattr(before, f) for f in BUSINESS_FIELDS}
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    after = repository.update_business(db, ctx, data.version, changes)
    if after is None:
        raise Conflict("org.business_version_conflict", current_version=before.version)
    audit.record(
        db,
        ctx,
        action="org.business.updated",
        target_type="business",
        target_id=after.id,
        summary=f"Business profile updated ({', '.join(sorted(changes)) or 'no fields'})",
        changes=audit.diff(snapshot, after, BUSINESS_FIELDS),
    )
    return after


def create_business(
    db: Session, ctx: Context, *, name: str, slug: str, timezone: str, currency: str, locale: str
) -> BusinessBrief:
    """Create a tenant with a default location and default settings. Used by identity's bootstrap only.

    `ctx` must carry the new business id (the caller generates it) so row-level security admits the inserts.
    """
    if not SLUG_RE.match(slug):
        raise Invalid("org.invalid_slug")
    validate_timezone(timezone)
    if ctx.business_id is None:
        raise Invalid("org.business_id_required")
    exists = db.execute(text("select 1 from public_business_by_slug(:s)"), {"s": slug}).first()
    if exists:
        raise Conflict("org.slug_taken")
    business = Business(
        id=ctx.business_id, slug=slug, name=name, timezone=timezone, currency=currency.upper(), locale=locale
    )
    db.add(business)
    db.flush()
    db.add(Location(business_id=business.id, name="Main location", timezone=timezone, is_default=True))
    db.add(
        BusinessSettingsRow(business_id=business.id, settings=BusinessSettings().model_dump(exclude={"sales_tax_rate"}))
    )
    db.flush()
    audit.record(
        db,
        ctx,
        action="org.business.created",
        target_type="business",
        target_id=business.id,
        summary=f"Business '{name}' created",
    )
    return brief(business)


# ----------------------------------------------------------------------------------------------- locations
def list_locations(db: Session, ctx: Context) -> list[Location]:
    ctx.require(P.LOCATION_READ)
    return repository.list_locations(db, ctx)


def get_location(db: Session, ctx: Context, location_id: uuid.UUID) -> Location:
    ctx.require(P.LOCATION_READ)
    location = repository.get_location(db, ctx, location_id)
    if location is None:
        raise NotFound("org.location_not_found")
    return location


def create_location(db: Session, ctx: Context, data: LocationCreate) -> Location:
    ctx.require(P.LOCATION_WRITE)
    business = _business(db, ctx)
    if data.is_default:
        repository.clear_default_location(db, ctx)
    location = Location(
        id=new_id(),
        business_id=business.id,
        name=data.name,
        address=data.address,
        timezone=data.timezone or business.timezone,
        is_default=data.is_default,
    )
    db.add(location)
    db.flush()
    audit.record(
        db,
        ctx,
        action="org.location.created",
        target_type="location",
        target_id=location.id,
        summary=f"Location '{data.name}' created",
    )
    return location


def update_location(db: Session, ctx: Context, location_id: uuid.UUID, data: LocationUpdate) -> Location:
    ctx.require(P.LOCATION_WRITE)
    before = get_location(db, ctx, location_id)
    snapshot = {f: getattr(before, f) for f in ("name", "address", "timezone", "is_default")}
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    if changes.get("is_default") is False and before.is_default:
        raise Invalid("org.default_location_required")
    if changes.get("is_default") and not before.is_default:
        if before.version != data.version:
            raise Conflict("org.location_version_conflict", current_version=before.version)
        repository.clear_default_location(db, ctx)
    after = repository.update_location(db, ctx, location_id, data.version, changes)
    if after is None:
        raise Conflict("org.location_version_conflict", current_version=before.version)
    audit.record(
        db,
        ctx,
        action="org.location.updated",
        target_type="location",
        target_id=location_id,
        summary=f"Location '{after.name}' updated",
        changes=audit.diff(snapshot, after, list(snapshot)),
    )
    return after


# ------------------------------------------------------------------------------------------- opening hours
def get_opening_hours(db: Session, ctx: Context, location_id: uuid.UUID) -> list[OpeningHours]:
    get_location(db, ctx, location_id)
    return repository.get_opening_hours(db, ctx, location_id)


def _check_no_overlap(items: list[OpeningHoursItem]) -> None:
    by_day: dict[int, list[tuple[time, time]]] = {}
    for item in items:
        if item.closes <= item.opens:
            raise Invalid("org.invalid_hours", detail=f"weekday {item.weekday}: closes must be after opens")
        by_day.setdefault(item.weekday, []).append((item.opens, item.closes))
    for day, spans in by_day.items():
        spans.sort()
        for (_, end), (start, _) in itertools.pairwise(spans):
            if start < end:
                raise Invalid("org.invalid_hours", detail=f"weekday {day}: overlapping intervals")


def replace_opening_hours(
    db: Session, ctx: Context, location_id: uuid.UUID, items: list[OpeningHoursItem]
) -> list[OpeningHours]:
    ctx.require(P.LOCATION_WRITE)
    get_location(db, ctx, location_id)
    _check_no_overlap(items)
    assert ctx.business_id is not None
    rows = [
        OpeningHours(
            business_id=ctx.business_id, location_id=location_id, weekday=i.weekday, opens=i.opens, closes=i.closes
        )
        for i in items
    ]
    repository.replace_opening_hours(db, ctx, location_id, rows)
    audit.record(
        db,
        ctx,
        action="org.opening_hours.replaced",
        target_type="location",
        target_id=location_id,
        summary=f"Opening hours replaced ({len(rows)} intervals)",
    )
    events.publish(db, ctx, OpeningHoursChanged(location_id=location_id))
    return repository.get_opening_hours(db, ctx, location_id)


# ------------------------------------------------------------------------------------------------ settings
def _settings_row(db: Session, ctx: Context) -> BusinessSettingsRow:
    row = repository.get_settings_row(db, ctx)
    if row is None:
        raise NotFound("org.settings_not_found")
    return row


def _settings_value(row: BusinessSettingsRow) -> BusinessSettings:
    return BusinessSettings.model_validate({**row.settings, "sales_tax_rate": row.sales_tax_rate})


def read_settings(db: Session, ctx: Context) -> BusinessSettings:
    """Settings for internal use by other modules (no permission needed: they gate their own features)."""
    return _settings_value(_settings_row(db, ctx))


def get_settings(db: Session, ctx: Context) -> tuple[BusinessSettings, int]:
    ctx.require(P.SETTINGS_READ)
    row = _settings_row(db, ctx)
    return _settings_value(row), row.version


def update_settings(
    db: Session, ctx: Context, new: BusinessSettings, expected_version: int
) -> tuple[BusinessSettings, int]:
    ctx.require(P.SETTINGS_WRITE)
    row = _settings_row(db, ctx)
    old = _settings_value(row)
    old_dump, new_dump = old.model_dump(), new.model_dump()
    changed = sorted(k for k in new_dump if new_dump[k] != old_dump[k])
    if (set(changed) & {"ai_policy", "ai_daily_token_budget", "ai_reply_guardrail"}) and not ctx.has(
        P.AI_SETTINGS_WRITE
    ):
        raise Forbidden("auth.permission_denied", detail=P.AI_SETTINGS_WRITE)
    updated = repository.update_settings_row(
        db,
        ctx,
        expected_version,
        new.model_dump(mode="json", exclude={"sales_tax_rate"}),
        sales_tax_rate=new.sales_tax_rate,
    )
    if updated is None:
        raise Conflict("org.settings_version_conflict", current_version=row.version)
    audit.record(
        db,
        ctx,
        action="org.settings.updated",
        target_type="business_settings",
        target_id=row.id,
        summary=f"Settings changed: {', '.join(changed) or 'none'}",
        changes=audit.diff(old_dump, new_dump, changed),
    )
    if changed:
        events.publish(db, ctx, SettingsChanged(changed_keys=changed))
    return new, updated.version
