"""Public API of the org module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.errors import NotFound

from . import repository, service
from .schemas import BusinessBrief, BusinessSettings, LocationOut, OpeningHoursItem


def get_business_brief(db: Session, ctx: Context) -> BusinessBrief:
    business = repository.get_business(db, ctx)
    if business is None:
        raise NotFound("org.business_not_found")
    return service.brief(business)


def get_public_email(db: Session, ctx: Context) -> str | None:
    """Public business contact, for notification Reply-To (no staff-setting permission needed)."""
    business = repository.get_business(db, ctx)
    if business is None:
        raise NotFound("org.business_not_found")
    email = business.public_profile.get("email")
    return email if isinstance(email, str) and email else None


def customer_care_profile(db: Session, ctx: Context) -> dict[str, object]:
    """Existing public profile; links and minimal-message switch only."""
    business = repository.get_business(db, ctx)
    if business is None:
        raise NotFound("org.business_not_found")
    return {k: business.public_profile.get(k) for k in ("review_url", "booking_url", "avoid_medical_details")}


def get_settings(db: Session, ctx: Context) -> BusinessSettings:
    return service.read_settings(db, ctx)


def get_sales_tax_rate(db: Session, ctx: Context) -> Decimal:
    """Exact business default for new sales lines; callers gate their own document writes."""
    return service.read_settings(db, ctx).sales_tax_rate


def get_default_location(db: Session, ctx: Context) -> LocationOut:
    location = repository.get_default_location(db, ctx)
    if location is None:
        raise NotFound("org.location_not_found")
    return LocationOut.model_validate(location)


def get_opening_hours(db: Session, ctx: Context, location_id: uuid.UUID | None = None) -> list[OpeningHoursItem]:
    target = location_id or get_default_location(db, ctx).id
    return [OpeningHoursItem.model_validate(row) for row in repository.get_opening_hours(db, ctx, target)]


create_business = service.create_business

__all__ = [
    "BusinessBrief",
    "BusinessSettings",
    "LocationOut",
    "OpeningHoursItem",
    "autonomy_paused",
    "create_business",
    "get_business_brief",
    "get_default_location",
    "get_opening_hours",
    "get_public_email",
    "get_sales_tax_rate",
    "get_settings",
    "set_autonomy_paused",
]


def autonomy_paused(db: Session, ctx: Context, *, lock: bool = False) -> bool:
    """Read the kill switch; share lock serializes policy execution against settings writes."""
    from sqlalchemy import select

    from .models import BusinessSettingsRow

    stmt = select(BusinessSettingsRow).where(BusinessSettingsRow.business_id == ctx.business_id)
    if lock:
        stmt = stmt.with_for_update(read=True)
    row = db.scalar(stmt.execution_options(populate_existing=True))
    if row is None:
        raise NotFound("org.settings_not_found")
    return BusinessSettings.model_validate(row.settings).ai_autonomy_paused


def set_autonomy_paused(db: Session, ctx: Context, paused: bool) -> None:
    """Change only the kill switch under the settings row lock; owner/admin permission."""
    from sqlalchemy import select

    from app.platform import audit, events

    from .events import SettingsChanged
    from .models import BusinessSettingsRow

    ctx.require("org.settings.write")
    row = db.scalar(
        select(BusinessSettingsRow)
        .where(BusinessSettingsRow.business_id == ctx.business_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("org.settings_not_found")
    if BusinessSettings.model_validate(row.settings).ai_autonomy_paused == paused:
        return
    row.settings = {**row.settings, "ai_autonomy_paused": paused}
    row.version += 1
    audit.record(
        db,
        ctx,
        action="org.settings.updated",
        target_type="business_settings",
        target_id=row.id,
        summary="AI autonomy paused" if paused else "AI autonomy resumed",
        changes={"ai_autonomy_paused": {"from": not paused, "to": paused}},
    )
    events.publish(db, ctx, SettingsChanged(changed_keys=["ai_autonomy_paused"]))


def get_location(db: Session, ctx: Context, location_id: uuid.UUID) -> LocationOut:
    from app.platform.crud import require_scoped

    from .models import Location

    return LocationOut.model_validate(require_scoped(db, ctx, Location, location_id, "org.location_not_found"))
