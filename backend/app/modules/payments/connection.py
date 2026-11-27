"""Setting up online payments for a business: relay registration, the Stripe account, onboarding, readiness.

Network calls never run inside a transaction that holds the connection's row lock: each step reads what it needs,
talks to the relay, then stores the result in a short transaction of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.org.public import get_business_brief, get_public_email
from app.platform import audit, clock
from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict

from . import permissions as P
from . import provider
from .crypto import UnsealError, seal, unseal
from .models import PaymentConnection
from .relay import AccountView, Credentials, RelayError
from .schemas import PaymentConnectionOut, StripeOnboardingIn, StripeOnboardingOut

DASHBOARD = "https://dashboard.stripe.com"


def get(db: Session, ctx: Context, *, lock: bool = False) -> PaymentConnection | None:
    stmt = select(PaymentConnection).where(PaymentConnection.business_id == ctx.business_id)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def failure(exc: RelayError) -> Conflict:
    codes = {
        "unavailable": "payments.relay_unavailable",
        "auth": "payments.relay_auth",
        "not_found": "payments.no_account",
        "refused": "payments.stripe_refused",
    }
    return Conflict(codes.get(exc.kind, "payments.relay_unavailable"), detail=exc.message)


def status_for(view: AccountView) -> str:
    if view.charges_enabled:
        return "active"
    return "restricted" if view.details_submitted else "onboarding"


def credentials(conn: PaymentConnection) -> Credentials:
    if not conn.relay_client_id or not conn.relay_secret_sealed:
        raise Conflict("payments.not_connected", detail="Set up online payments first")
    try:
        return Credentials(conn.relay_client_id, unseal(conn.relay_secret_sealed))
    except UnsealError:
        raise Conflict(
            "payments.credential_unreadable", detail="The stored relay credential cannot be read (secret key changed?)"
        ) from None


def _dashboard(conn: PaymentConnection) -> str | None:
    if not conn.stripe_account_id:
        return None
    return f"{DASHBOARD}/test" if conn.mode == "test" else DASHBOARD


def out(db: Session, ctx: Context) -> PaymentConnectionOut:
    ctx.require(P.CONNECTION_READ)
    business = get_business_brief(db, ctx)
    conn = get(db, ctx)
    if conn is None or conn.stripe_account_id is None:
        return PaymentConnectionOut(
            configured=provider.configured(),
            status="not_connected",
            business_currency=business.currency,
            last_error=conn.last_error if conn else None,
            version=conn.version if conn else None,
        )
    return PaymentConnectionOut(
        configured=provider.configured(),
        status=conn.status,
        mode=conn.mode,
        business_currency=business.currency,
        stripe_account_id=conn.stripe_account_id,
        country=conn.country,
        default_currency=conn.default_currency.upper() if conn.default_currency else None,
        charges_enabled=conn.charges_enabled,
        payouts_enabled=conn.payouts_enabled,
        details_submitted=conn.details_submitted,
        requirements_due=[str(x) for x in conn.requirements_due or []],
        disabled_reason=conn.disabled_reason,
        connected_at=conn.connected_at,
        last_checked_at=conn.last_checked_at,
        last_error=conn.last_error,
        dashboard_url=_dashboard(conn),
        version=conn.version,
    )


def apply_view(conn: PaymentConnection, view: AccountView, *, reconnect: bool = False) -> bool:
    """Store what Stripe says about the account. Returns True when anything the settings page shows changed."""
    before = (conn.status, conn.charges_enabled, conn.payouts_enabled, conn.details_submitted, conn.requirements_due)
    due = sorted(set(view.currently_due) | set(view.past_due))
    conn.stripe_account_id = view.id
    conn.country = view.country
    conn.default_currency = view.default_currency
    conn.charges_enabled = view.charges_enabled
    conn.payouts_enabled = view.payouts_enabled
    conn.details_submitted = view.details_submitted
    conn.requirements_due = due
    conn.disabled_reason = (view.disabled_reason or "")[:100] or None
    if conn.status != "disconnected" or reconnect:
        conn.status = status_for(view)
    conn.last_checked_at = clock.now()
    conn.last_error = None
    changed = before != (conn.status, conn.charges_enabled, conn.payouts_enabled, conn.details_submitted, due)
    conn.version += 1
    return changed


def _check_offered(ctx: Context) -> None:
    ctx.require(P.CONNECTION_MANAGE)
    if is_regulated():
        raise Conflict(
            "payments.regulated_profile",
            detail="Online payments are off for the regulated data profile (customer names would leave this server)",
        )
    if not provider.configured():
        raise Conflict("payments.not_configured", detail="Set PAYMENTS_RELAY_URL to offer online payments")


@dataclass(frozen=True, slots=True)
class _Plan:
    creds: Credentials | None
    name: str
    email: str | None


def _prepare(ctx: Context) -> _Plan:
    with transaction(ctx) as db:
        business = get_business_brief(db, ctx)
        email = get_public_email(db, ctx)
        conn = get(db, ctx, lock=True)
        if conn is None:
            conn = PaymentConnection(
                business_id=ctx.business_id, mode="test", status="onboarding", relay_url=provider.relay_url()
            )
            db.add(conn)
            db.flush()
            audit.record(
                db,
                ctx,
                action="payments.setup_started",
                target_type="payment_connection",
                target_id=conn.id,
                summary="Started setting up online payments with Stripe",
            )
        elif conn.relay_client_id and conn.relay_url != provider.relay_url():
            raise Conflict(
                "payments.relay_changed",
                detail="This business is registered with another payments relay; ask the relay operator to move it",
            )
        creds = credentials(conn) if conn.relay_client_id else None
        return _Plan(creds, business.name, email)


def _register(ctx: Context, name: str) -> Credentials:
    settings_token = provider.enrollment_token()
    with provider.http_client() as client:
        try:
            reg = provider.api(client).register(
                name=name,
                business_ref=str(ctx.business_id),
                base_url=provider.app_base_url(),
                events_url=provider.events_url(),
                enrollment_token=settings_token,
            )
        except RelayError as exc:
            raise failure(exc) from None
    with transaction(ctx) as db:
        conn = get(db, ctx, lock=True)
        assert conn is not None
        if conn.relay_client_id:  # a concurrent setup registered first: use its credential, ours stays unused
            return credentials(conn)
        conn.relay_client_id = reg.client_id
        conn.relay_secret_sealed = seal(reg.secret)
        conn.mode = "live" if reg.mode == "live" else "test"
        conn.version += 1
        return Credentials(reg.client_id, reg.secret)


def _record_error(ctx: Context, message: str) -> None:
    with transaction(ctx) as db:
        conn = get(db, ctx, lock=True)
        if conn is not None:
            conn.last_error = message[:500]
            conn.version += 1


def start_onboarding(ctx: Context, body: StripeOnboardingIn) -> StripeOnboardingOut:
    """Register with the relay if needed, create the Stripe account if needed, and return a fresh onboarding link.

    Onboarding links are single use and expire within minutes, so one is made on every click and never stored
    or emailed (M21 rule 9).
    """
    _check_offered(ctx)
    plan = _prepare(ctx)
    creds = plan.creds or _register(ctx, plan.name)
    with provider.http_client() as client:
        relay = provider.api(client)
        try:
            view = relay.create_account(creds, country=body.country, email=plan.email, name=plan.name)
            url = relay.onboarding_link(creds)
        except RelayError as exc:
            _record_error(ctx, exc.message)
            raise failure(exc) from None
    with transaction(ctx) as db:
        conn = get(db, ctx, lock=True)
        assert conn is not None
        first = conn.stripe_account_id is None
        was_disconnected = conn.status == "disconnected"
        apply_view(conn, view, reconnect=True)
        if first:
            conn.connected_by, conn.connected_at = ctx.user_id, clock.now()
        if first or was_disconnected:
            audit.record(
                db,
                ctx,
                action="payments.account_linked" if first else "payments.reconnected",
                target_type="payment_connection",
                target_id=conn.id,
                summary=f"Linked Stripe account {view.id} for online payments"
                if first
                else "Turned online payments back on",
            )
    return StripeOnboardingOut(url=url)


def refresh(ctx: Context) -> None:
    """Read the account's readiness from Stripe now (also run for every account event the relay forwards)."""
    with transaction(ctx) as db:
        conn = get(db, ctx)
        if conn is None or conn.stripe_account_id is None:
            raise Conflict("payments.not_connected", detail="Set up online payments first")
        creds = credentials(conn)
    with provider.http_client() as client:
        try:
            view = provider.api(client).get_account(creds)
        except RelayError as exc:
            _record_error(ctx, exc.message)
            raise failure(exc) from None
    with transaction(ctx) as db:
        conn = get(db, ctx, lock=True)
        assert conn is not None
        if conn.stripe_account_id != view.id:
            raise Conflict("payments.account_mismatch", detail="The relay returned a different Stripe account")
        apply_view(conn, view)


def disconnect(db: Session, ctx: Context, version: int) -> None:
    """Stop offering online payments. The Stripe account stays the business's own; setting up again relinks it."""
    ctx.require(P.CONNECTION_MANAGE)
    conn = get(db, ctx, lock=True)
    if conn is None or conn.stripe_account_id is None:
        raise Conflict("payments.not_connected", detail="Online payments are not set up")
    if conn.version != version:
        raise Conflict("payments.version_conflict", current_version=conn.version)
    if conn.status == "disconnected":
        return
    conn.status = "disconnected"
    conn.version += 1
    audit.record(
        db,
        ctx,
        action="payments.disconnected",
        target_type="payment_connection",
        target_id=conn.id,
        summary="Turned online payments off (the Stripe account stays the business's own)",
    )


def from_unix(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None
