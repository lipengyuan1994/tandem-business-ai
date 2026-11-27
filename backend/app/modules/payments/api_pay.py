"""The public pay page behind an invoice's pay link. No login: the signed token selects the business and invoice.

Never log tokens (the reverse proxy masks them in access logs). Both endpoints are rate limited per address and
per link.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.platform import ratelimit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses

from . import paylinks, tokens
from .schemas import CheckoutStartOut, PayPageOut

router = APIRouter()
ERRORS = error_responses(404, 409, 429)


def pay_context(token: str, request: Request) -> Context:
    business_id, _, _ = tokens.read_token(token)
    return Context(
        request_id=getattr(request.state, "request_id", "-"),
        actor_kind="visitor",
        user_id=None,
        business_id=business_id,
        permissions=frozenset({"public"}),
    )


pay_context.smb_permission = "public"  # type: ignore[attr-defined]


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/{token}", response_model=PayPageOut, summary="Read an invoice's pay page", responses=ERRORS)
def pay_page(token: str, request: Request, response: Response, ctx: Context = Depends(pay_context)) -> PayPageOut:
    response.headers["Cache-Control"] = "no-store"  # a capability page: nothing may keep a copy
    with transaction(ctx) as db:
        ratelimit.hit(db, f"pay-read-ip:{_ip(request)}", limit=120, window_seconds=60)
        ratelimit.hit(db, f"pay-read:{tokens.token_hash(token)[:32]}", limit=60, window_seconds=60)
        return paylinks.page(db, ctx, token)


@router.post(
    "/{token}/checkout",
    response_model=CheckoutStartOut,
    summary="Open Stripe's payment page for the invoice's open balance",
    responses=ERRORS,
)
def checkout(token: str, request: Request, response: Response, ctx: Context = Depends(pay_context)) -> CheckoutStartOut:
    response.headers["Cache-Control"] = "no-store"
    with transaction(ctx) as db:
        ratelimit.hit(db, f"pay-checkout-ip:{_ip(request)}", limit=30, window_seconds=3600)
        ratelimit.hit(db, f"pay-checkout:{tokens.token_hash(token)[:32]}", limit=10, window_seconds=600)
    return paylinks.start_checkout(ctx, token)
