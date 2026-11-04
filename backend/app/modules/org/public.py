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
    "create_business",
    "get_business_brief",
    "get_default_location",
    "get_opening_hours",
    "get_public_email",
    "get_sales_tax_rate",
    "get_settings",
]
