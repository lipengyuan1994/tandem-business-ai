"""Staff endpoints for Settings > Payments, and the endpoint the payments relay posts Stripe events to."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError

from app.platform import ratelimit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Invalid, error_responses
from app.platform.permissions import require

from . import connection, events, paylinks, payouts, refunds, signing
from . import permissions as P
from .schemas import (
    CardRefundIn,
    CardRefundProposedOut,
    InvoiceOnlinePaymentsOut,
    PayLinkOut,
    PaymentConnectionOut,
    PaymentDisconnectIn,
    PayoutOut,
    RelayEventIn,
    RelayEventOut,
    StripeOnboardingIn,
    StripeOnboardingOut,
)

router = APIRouter()
relay_router = APIRouter()
ERRORS = error_responses(400, 401, 403, 404, 409, 422)


@router.get(
    "/stripe",
    response_model=PaymentConnectionOut,
    summary="Online payments: Stripe account and readiness",
    responses=ERRORS,
)
def status(ctx: Context = Depends(require(P.CONNECTION_READ))) -> PaymentConnectionOut:
    with transaction(ctx) as db:
        return connection.out(db, ctx)


@router.post(
    "/stripe/onboarding",
    response_model=StripeOnboardingOut,
    summary="Set up or continue Stripe onboarding (returns a fresh, single-use Stripe link)",
    responses=ERRORS,
)
def onboarding(body: StripeOnboardingIn, ctx: Context = Depends(require(P.CONNECTION_MANAGE))) -> StripeOnboardingOut:
    return connection.start_onboarding(ctx, body)


@router.post(
    "/stripe/refresh",
    response_model=PaymentConnectionOut,
    summary="Read the Stripe account's readiness now",
    responses=ERRORS,
)
def refresh(ctx: Context = Depends(require(P.CONNECTION_MANAGE))) -> PaymentConnectionOut:
    connection.refresh(ctx)
    with transaction(ctx) as db:
        return connection.out(db, ctx)


@router.post(
    "/stripe/disconnect",
    response_model=PaymentConnectionOut,
    summary="Turn online payments off (the Stripe account stays the business's own)",
    responses=ERRORS,
)
def disconnect(body: PaymentDisconnectIn, ctx: Context = Depends(require(P.CONNECTION_MANAGE))) -> PaymentConnectionOut:
    with transaction(ctx) as db:
        connection.disconnect(db, ctx, body.version)
    with transaction(ctx) as db:
        return connection.out(db, ctx)


@router.get(
    "/payouts",
    response_model=list[PayoutOut],
    summary="Stripe payouts to the business's bank, newest first",
    responses=ERRORS,
)
def list_payouts(ctx: Context = Depends(require(P.CONNECTION_READ))) -> list[PayoutOut]:
    with transaction(ctx) as db:
        return payouts.recent(db, ctx)


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceOnlinePaymentsOut,
    summary="An invoice's pay link and online payments",
    responses=ERRORS,
)
def invoice_payments(
    invoice_id: uuid.UUID, response: Response, ctx: Context = Depends(require(P.LINK_SHARE))
) -> InvoiceOnlinePaymentsOut:
    response.headers["Cache-Control"] = "no-store"  # carries the pay link, a capability
    with transaction(ctx) as db:
        return paylinks.summary(db, ctx, invoice_id)


@router.post(
    "/invoices/{invoice_id}/link",
    response_model=PayLinkOut,
    summary="Get the invoice's pay link (made on first use) to send to the customer",
    responses=ERRORS,
)
def share_link(invoice_id: uuid.UUID, response: Response, ctx: Context = Depends(require(P.LINK_SHARE))) -> PayLinkOut:
    response.headers["Cache-Control"] = "no-store"  # carries the pay link, a capability
    with transaction(ctx) as db:
        return paylinks.share(db, ctx, invoice_id)


@router.post(
    "/invoices/{invoice_id}/link/reset",
    response_model=PayLinkOut,
    summary="Replace the invoice's pay link; the old link stops working",
    responses=ERRORS,
)
def reset_link(invoice_id: uuid.UUID, response: Response, ctx: Context = Depends(require(P.LINK_SHARE))) -> PayLinkOut:
    response.headers["Cache-Control"] = "no-store"  # carries the pay link, a capability
    with transaction(ctx) as db:
        return paylinks.share(db, ctx, invoice_id, reset=True)


@router.post(
    "/invoices/{invoice_id}/card-refunds",
    response_model=CardRefundProposedOut,
    status_code=201,
    summary="Ask to pay money back to the customer's card (waits for an owner or admin to approve)",
    responses=ERRORS,
)
def propose_card_refund(
    invoice_id: uuid.UUID, body: CardRefundIn, ctx: Context = Depends(require(P.REFUND_PROPOSE))
) -> CardRefundProposedOut:
    with transaction(ctx) as db:
        return refunds.propose(db, ctx, invoice_id, body)


async def relay_event(request: Request) -> tuple[RelayEventIn, bytes]:
    body = await request.body()
    if len(body) > 16_384:
        raise Invalid("payments.event_too_large")
    try:
        return RelayEventIn.model_validate_json(body), body
    except ValidationError:
        raise Invalid("payments.event_invalid", detail="Not a relay event envelope") from None


def relay_context(request: Request, parsed: tuple[RelayEventIn, bytes] = Depends(relay_event)) -> Context:
    """The envelope names the business; the signature (checked in events.receive) proves the relay sent it."""
    return Context(
        request_id=getattr(request.state, "request_id", "-"),
        actor_kind="visitor",
        user_id=None,
        business_id=parsed[0].business_ref,
        permissions=frozenset({"public"}),
    )


relay_context.smb_permission = "public"  # type: ignore[attr-defined]


@relay_router.post(
    "/events",
    response_model=RelayEventOut,
    summary="Stripe event forwarded by the payments relay (signed; no login)",
    responses=error_responses(401, 409, 422, 429),
)
def relay_events(
    request: Request,
    parsed: tuple[RelayEventIn, bytes] = Depends(relay_event),
    ctx: Context = Depends(relay_context),
) -> RelayEventOut:
    event, body = parsed
    with transaction(ctx) as db:
        ip = request.client.host if request.client else "unknown"
        ratelimit.hit(db, f"payments-relay-ip:{ip}", limit=600, window_seconds=60)
        stored = events.receive(
            db,
            ctx,
            event,
            body,
            request.headers.get(signing.CLIENT_HEADER, ""),
            request.headers.get(signing.SIGNATURE_HEADER, ""),
        )
    return RelayEventOut(status="accepted" if stored else "duplicate")
