"""Public self-service endpoints; capability cookies never grant staff permissions directly."""

from __future__ import annotations

import secrets
import time
import uuid

from fastapi import APIRouter, Depends, Request, Response
from pydantic import AwareDatetime

from app.modules.crm import public as crm
from app.platform import ratelimit
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Forbidden
from app.platform.permissions import hash_token, public_context

from . import self_service as ss
from .self_service_schemas import (
    CustomerBooking,
    CustomerBookings,
    CustomerChange,
    CustomerReschedule,
    CustomerSlot,
    LinkExchange,
    LinkRequest,
)

router = APIRouter(prefix="/{business_slug}/self-service")


def origin(request: Request) -> None:
    if request.headers.get("origin") != get_settings().app_base_url.rstrip("/"):
        raise Forbidden("auth.csrf_failed")


def cookie(response: Response, slug: str, token: str) -> None:
    response.set_cookie(
        ss.COOKIE,
        token,
        max_age=3600,
        httponly=True,
        secure=True,
        samesite="strict",
        path=f"/api/v1/public/{slug}/self-service/",
    )
    response.headers["Cache-Control"] = "no-store"


def session(db: object, ctx: Context, request: Request, response: Response, slug: str) -> uuid.UUID:
    from sqlalchemy.orm import Session

    assert isinstance(db, Session)
    token = request.cookies.get(ss.COOKIE)
    ratelimit.hit(db, f"ss-session:{hash_token(token or ip(request))}", limit=120, window_seconds=60)
    cid = ss.authenticate(db, ctx, token)
    assert token is not None
    cookie(response, slug, token)
    return cid


def ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/request", status_code=202)
def request_link(body: LinkRequest, request: Request, ctx: Context = Depends(public_context)) -> dict[str, str]:
    origin(request)
    started = time.monotonic()
    try:
        with transaction(ctx) as db:
            ratelimit.hit(db, f"ss-ip:{hash_token(ip(request))}", limit=10, window_seconds=3600)
            normalized = crm.normalize_contact("email", str(body.email))
            ratelimit.hit(db, f"ss-email:{ctx.business_id}:{hash_token(normalized)}", limit=3, window_seconds=3600)
            ss.request_link(db, ctx, normalized, nonce=secrets.token_hex(16))
    finally:
        # Same minimum timing class for all normal outcomes, with no SMTP or other network work here.
        remaining = 0.25 - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
    return {"message": ss.NEUTRAL}


@router.post("/exchange")
def exchange(
    business_slug: str, body: LinkExchange, request: Request, response: Response, ctx: Context = Depends(public_context)
) -> dict[str, bool]:
    origin(request)
    with transaction(ctx) as db:
        ratelimit.hit(db, f"ss-exchange:{hash_token(ip(request))}", limit=20, window_seconds=60)
        token = ss.exchange(db, ctx, body.token, previous_session=request.cookies.get(ss.COOKIE))
    cookie(response, business_slug, token)
    return {"authenticated": True}


@router.get("/bookings", response_model=CustomerBookings)
def bookings(
    business_slug: str, request: Request, response: Response, ctx: Context = Depends(public_context)
) -> CustomerBookings:
    with transaction(ctx) as db:
        cid = session(db, ctx, request, response, business_slug)
        return CustomerBookings(items=ss.bookings(db, ctx, cid))


@router.get("/bookings/{appointment_id}/slots", response_model=list[CustomerSlot])
def slots(
    business_slug: str,
    appointment_id: uuid.UUID,
    earliest: AwareDatetime,
    latest: AwareDatetime,
    request: Request,
    response: Response,
    ctx: Context = Depends(public_context),
) -> list[CustomerSlot]:
    with transaction(ctx) as db:
        cid = session(db, ctx, request, response, business_slug)
        return ss.slots(db, ss.customer_context(ctx), cid, appointment_id, earliest, latest)


@router.post("/bookings/{appointment_id}/cancel", response_model=CustomerBooking)
def cancel(
    business_slug: str,
    appointment_id: uuid.UUID,
    body: CustomerChange,
    request: Request,
    response: Response,
    ctx: Context = Depends(public_context),
) -> CustomerBooking:
    origin(request)
    with transaction(ctx) as db:
        cid = session(db, ctx, request, response, business_slug)
        return ss.change(db, ctx, cid, appointment_id, body, reschedule=False)


@router.post("/bookings/{appointment_id}/reschedule", response_model=CustomerBooking)
def reschedule(
    business_slug: str,
    appointment_id: uuid.UUID,
    body: CustomerReschedule,
    request: Request,
    response: Response,
    ctx: Context = Depends(public_context),
) -> CustomerBooking:
    origin(request)
    with transaction(ctx) as db:
        cid = session(db, ctx, request, response, business_slug)
        return ss.change(db, ctx, cid, appointment_id, body, reschedule=True)
