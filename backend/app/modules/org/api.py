"""HTTP endpoints for the org module (/api/v1/org/..., /api/v1/public/{slug}/profile)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import public_context, require

from . import permissions as P
from . import public, repository, service
from .schemas import (
    BusinessOut,
    BusinessUpdate,
    LocationCreate,
    LocationOut,
    LocationUpdate,
    OpeningHoursItem,
    OpeningHoursWeek,
    PublicProfileOut,
    SettingsOut,
    SettingsUpdate,
)

router = APIRouter()
public_router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


@router.get("/business", response_model=BusinessOut, summary="Get the active business", responses=ERRORS)
def get_business(ctx: Context = Depends(require(P.BUSINESS_READ))) -> BusinessOut:
    with transaction(ctx) as db:
        return BusinessOut.model_validate(service.get_business(db, ctx))


@router.patch("/business", response_model=BusinessOut, summary="Update the business profile", responses=ERRORS)
def update_business(body: BusinessUpdate, ctx: Context = Depends(require(P.BUSINESS_WRITE))) -> BusinessOut:
    with transaction(ctx) as db:
        return BusinessOut.model_validate(service.update_business(db, ctx, body))


@router.get("/locations", response_model=list[LocationOut], summary="List locations", responses=ERRORS)
def list_locations(ctx: Context = Depends(require(P.LOCATION_READ))) -> list[LocationOut]:
    with transaction(ctx) as db:
        return [LocationOut.model_validate(x) for x in service.list_locations(db, ctx)]


@router.post("/locations", response_model=LocationOut, status_code=201, summary="Create a location", responses=ERRORS)
def create_location(body: LocationCreate, ctx: Context = Depends(require(P.LOCATION_WRITE))) -> LocationOut:
    with transaction(ctx) as db:
        return LocationOut.model_validate(service.create_location(db, ctx, body))


@router.patch("/locations/{location_id}", response_model=LocationOut, summary="Update a location", responses=ERRORS)
def update_location(
    location_id: uuid.UUID, body: LocationUpdate, ctx: Context = Depends(require(P.LOCATION_WRITE))
) -> LocationOut:
    with transaction(ctx) as db:
        return LocationOut.model_validate(service.update_location(db, ctx, location_id, body))


@router.get(
    "/locations/{location_id}/opening-hours",
    response_model=list[OpeningHoursItem],
    summary="Weekly opening hours",
    responses=ERRORS,
)
def get_opening_hours(
    location_id: uuid.UUID, ctx: Context = Depends(require(P.LOCATION_READ))
) -> list[OpeningHoursItem]:
    with transaction(ctx) as db:
        return [OpeningHoursItem.model_validate(x) for x in service.get_opening_hours(db, ctx, location_id)]


@router.put(
    "/locations/{location_id}/opening-hours",
    response_model=list[OpeningHoursItem],
    summary="Replace the whole week of opening hours",
    responses=ERRORS,
)
def replace_opening_hours(
    location_id: uuid.UUID, body: OpeningHoursWeek, ctx: Context = Depends(require(P.LOCATION_WRITE))
) -> list[OpeningHoursItem]:
    with transaction(ctx) as db:
        rows = service.replace_opening_hours(db, ctx, location_id, body.items)
        return [OpeningHoursItem.model_validate(x) for x in rows]


@router.get("/settings", response_model=SettingsOut, summary="Business settings", responses=ERRORS)
def get_settings(ctx: Context = Depends(require(P.SETTINGS_READ))) -> SettingsOut:
    with transaction(ctx) as db:
        settings, version = service.get_settings(db, ctx)
        return SettingsOut(settings=settings, version=version)


@router.patch(
    "/settings",
    response_model=SettingsOut,
    summary="Replace business settings, including the default sales tax rate",
    responses=ERRORS,
)
def update_settings(body: SettingsUpdate, ctx: Context = Depends(require(P.SETTINGS_WRITE))) -> SettingsOut:
    with transaction(ctx) as db:
        settings, version = service.update_settings(db, ctx, body.settings, body.version)
        return SettingsOut(settings=settings, version=version)


@public_router.get("/{business_slug}/profile", response_model=PublicProfileOut, summary="Public business profile")
def public_profile(ctx: Context = Depends(public_context)) -> PublicProfileOut:
    with transaction(ctx) as db:
        business = repository.get_business(db, ctx)
        assert business is not None
        return PublicProfileOut(
            avoid_medical_details=is_regulated(),
            name=business.name,
            timezone=business.timezone,
            public_profile=business.public_profile,
            opening_hours=public.get_opening_hours(db, ctx),
            ai_policy=public.get_settings(db, ctx).ai_policy,
        )
