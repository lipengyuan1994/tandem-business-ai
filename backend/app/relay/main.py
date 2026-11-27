"""The relay's HTTP API.

Tandem side (signed with the client's secret, see signing.py):
- `POST /v1/clients` registers one Tandem business and returns its client id and secret (once). Needs the
  enrollment token when RELAY_ENROLLMENT_TOKEN is set.
- `POST /v1/account` creates the business's connected Stripe account, or returns the one it already has.
- `GET /v1/account` reads the account's readiness.
- `POST /v1/account/onboarding_link` makes a fresh Stripe onboarding link (single use, short-lived).
- `POST /v1/checkout_sessions` makes a Stripe Checkout Session (a direct charge on the business's account) for one
  payment request; `GET /v1/checkout_sessions/{id}` reads one back and `POST /v1/checkout_sessions/{id}/expire`
  closes one. Sessions are always created and read with the caller's own account, so Stripe itself refuses a
  session that belongs to another business.
- `POST /v1/refunds` pays part of a charge back to the customer's card; `GET /v1/refunds/{id}` and
  `GET /v1/disputes/{id}` read one back (phase 3). All on the caller's own account.
- `GET /v1/payments/{payment_intent}/fee` gives a payment's Stripe fee and net; `GET /v1/payouts/{id}` reads a payout
  and `GET /v1/payouts/{id}/summary` totals what it paid out (charges, refunds, disputes, other, fees) (phase 4).
A client can only ever reach its own account: the account is found from the client, never named in the request.

Stripe side:
- `POST /v1/stripe/webhook` is the platform's one Connect webhook endpoint. Events are checked against Stripe's
  signature, stored once and forwarded to the owning business (delivery.py).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from . import delivery, signing
from .config import RelaySettings, get_settings
from .crypto import UnsealError, seal, unseal
from .db import Database, make_engine
from .models import RelayAccount, RelayClient, RelayEvent, RelayNonce
from .stripe import (
    Stripe,
    StripeError,
    account_view,
    add_to_summary,
    checkout_view,
    dispute_view,
    payment_fee_view,
    payout_view,
    refund_view,
    verify_webhook,
)

log = structlog.get_logger(__name__)
NONCE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
SETTINGS_PATH = "/settings/payments"
SESSION_ID = re.compile(r"^cs_[A-Za-z0-9_]{1,200}$")
REFUND_ID = re.compile(r"^(re|pyr)_[A-Za-z0-9]{1,100}$")
DISPUTE_ID = re.compile(r"^(dp|du)_[A-Za-z0-9]{1,100}$")
INTENT_ID = re.compile(r"^pi_[A-Za-z0-9]{1,100}$")
PAYOUT_ID = re.compile(r"^po_[A-Za-z0-9]{1,100}$")
SUMMARY_PAGES = 50  # 5,000 transactions; a bigger payout is summarised as incomplete
# Checkout pages return to Tandem's public pay page only, never to an arbitrary URL.
PAY_PATH = re.compile(r"^/pay/[A-Za-z0-9_.=-]{10,400}(\?[A-Za-z0-9_={}&-]{0,100})?$")
# Stripe accepts expires_at between 30 minutes and 24 hours ahead.
CHECKOUT_MIN_SECONDS, CHECKOUT_MAX_SECONDS = 30 * 60, 24 * 60 * 60


class RelayError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    business_ref: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=8, max_length=500)
    events_url: str = Field(min_length=8, max_length=500)


class AccountIn(BaseModel):
    country: str = Field(pattern=r"^[A-Z]{2}$")
    email: str | None = Field(default=None, max_length=254)
    name: str = Field(min_length=1, max_length=200)


class CheckoutIn(BaseModel):
    request_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    invoice_ref: str = Field(pattern=r"^[0-9a-f-]{36}$")
    amount: int = Field(gt=0, le=99_999_999)
    currency: str = Field(pattern=r"^[a-z]{3}$")
    description: str = Field(min_length=1, max_length=200)
    success_path: str = Field(max_length=500)
    cancel_path: str = Field(max_length=500)
    expires_at: int = Field(gt=0)
    bank_transfers: bool = False


class RefundIn(BaseModel):
    refund_ref: str = Field(pattern=r"^[0-9a-f-]{36}$")
    invoice_ref: str = Field(pattern=r"^[0-9a-f-]{36}$")
    payment_intent: str = Field(pattern=r"^pi_[A-Za-z0-9]{1,100}$")
    amount: int = Field(gt=0, le=99_999_999)


@dataclass
class Relay:
    settings: RelaySettings
    db: Database
    stripe_http: Callable[[], httpx.Client]
    callback_http: Callable[[], httpx.Client]
    resolver: delivery.Resolver
    clock: Callable[[], int]
    enrollments: dict[str, deque[int]] = field(default_factory=lambda: defaultdict(deque))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def stripe(self, http: httpx.Client) -> Stripe:
        if not self.settings.stripe_configured:
            raise RelayError(503, "stripe_not_configured", "The relay has no Stripe key yet")
        return Stripe(
            http,
            self.settings.stripe_secret_key.get_secret_value(),
            base=self.settings.stripe_api_base,
            version=self.settings.stripe_api_version,
        )

    def deliver(self) -> int:
        with self.callback_http() as http, self.db.session() as db:
            return delivery.deliver_due(db, http, self.settings, self.clock(), self.resolver)


def _default_http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0), follow_redirects=False)


def _stripe_failure(exc: StripeError) -> RelayError:
    if exc.retryable:
        return RelayError(503, "stripe_unavailable", exc.message)
    return RelayError(422, f"stripe_{exc.code}"[:60], exc.message)


@dataclass(frozen=True)
class Raw:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    ip: str


async def _raw(request: Request) -> Raw:
    return Raw(
        request.method,
        request.url.path,
        {k.lower(): v for k, v in request.headers.items()},
        await request.body(),
        request.client.host if request.client else "unknown",
    )


@dataclass(frozen=True)
class Caller:
    id: str
    business_ref: str
    base_url: str


def authenticate(relay: Relay, raw: Raw) -> Caller:
    """Check the client's signature and nonce, and record the nonce in its own transaction (so a request that
    fails later still cannot be replayed)."""
    refused = RelayError(401, "unauthorized", "Unknown client or bad signature")
    client_id = raw.headers.get(signing.CLIENT_HEADER.lower(), "")
    nonce = raw.headers.get(signing.NONCE_HEADER.lower(), "")
    now = relay.clock()
    with relay.db.session() as db:
        client = db.get(RelayClient, client_id) if client_id else None
        if client is None or client.disabled_at is not None or not NONCE.match(nonce):
            raise refused
        try:
            secret = unseal(relay.settings.secret_key.get_secret_value(), client.secret_sealed)
        except UnsealError:
            raise refused from None
        payload = signing.request_payload(raw.method, raw.path, nonce, raw.body)
        if not signing.verify(secret, raw.headers.get(signing.SIGNATURE_HEADER.lower(), ""), payload, now):
            raise refused
        db.execute(delete(RelayNonce).where(RelayNonce.expires_at < now))
        if db.get(RelayNonce, (client.id, nonce)) is not None:
            raise RelayError(401, "replayed", "This request was already received")
        db.add(RelayNonce(client_id=client.id, nonce=nonce, expires_at=now + 2 * signing.TOLERANCE_SECONDS))
        return Caller(client.id, client.business_ref, client.base_url)


def _parse[M: BaseModel](model: type[M], body: bytes) -> M:
    try:
        return model.model_validate_json(body or b"{}")
    except ValidationError as exc:
        raise RelayError(422, "invalid", str(exc.errors()[0].get("msg", "invalid body"))[:200]) from None


def _check_base_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise RelayError(422, "invalid_base_url", "base_url must be an http(s) URL without credentials")


def create_app(
    settings: RelaySettings | None = None,
    *,
    stripe_http: Callable[[], httpx.Client] | None = None,
    callback_http: Callable[[], httpx.Client] | None = None,
    resolver: delivery.Resolver | None = None,
    clock: Callable[[], int] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    relay = Relay(
        settings=settings,
        db=Database(make_engine(settings.database_url)),
        stripe_http=stripe_http or _default_http,
        callback_http=callback_http or _default_http,
        resolver=resolver or delivery.resolve,
        clock=clock or (lambda: int(time.time())),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = threading.Event()
        thread: threading.Thread | None = None
        if settings.deliver_interval_seconds:

            def sweep() -> None:
                while not stop.wait(settings.deliver_interval_seconds):
                    try:
                        relay.deliver()
                    except Exception:  # keep sweeping; a broken row must not stop deliveries
                        log.exception("relay.sweep_failed")

            thread = threading.Thread(target=sweep, name="relay-delivery", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=5)

    app = FastAPI(title="Tandem payments relay", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.relay = relay

    @app.exception_handler(RelayError)
    async def _relay_error(_: Request, exc: RelayError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "mode": settings.mode, "stripe_configured": settings.stripe_configured}

    # -- clients -------------------------------------------------------------------------------------------

    def _enroll(raw: Raw) -> dict[str, Any]:
        token = settings.enrollment_token.get_secret_value()
        given = raw.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if token and not secrets.compare_digest(token.encode(), given.encode()):
            raise RelayError(401, "enrollment_refused", "This relay needs an enrollment token")
        now = relay.clock()
        with relay.lock:
            seen = relay.enrollments[raw.ip]
            while seen and seen[0] <= now - 3600:
                seen.popleft()
            if len(seen) >= settings.enrollments_per_hour:
                raise RelayError(429, "rate_limited", "Too many registrations from this address")
            seen.append(now)
        body = _parse(ClientIn, raw.body)
        _check_base_url(body.base_url)
        try:
            delivery.check_url(body.events_url, settings, resolver=relay.resolver)
        except delivery.BadDestination as exc:
            raise RelayError(422, "invalid_events_url", str(exc)) from None
        client_id = "rc_" + secrets.token_hex(12)
        secret = secrets.token_urlsafe(32)
        with relay.db.session() as db:
            db.add(
                RelayClient(
                    id=client_id,
                    name=body.name,
                    business_ref=body.business_ref,
                    base_url=body.base_url.rstrip("/"),
                    events_url=body.events_url,
                    secret_sealed=seal(settings.secret_key.get_secret_value(), secret),
                    mode=settings.mode,
                    created_at=now,
                )
            )
        log.info("relay.client_registered", client=client_id)
        return {"client_id": client_id, "secret": secret, "mode": settings.mode}

    @app.post("/v1/clients", status_code=201)
    async def register(request: Request) -> dict[str, Any]:
        return await run_in_threadpool(_enroll, await _raw(request))

    # -- the client's connected account ---------------------------------------------------------------------

    def _account_id(client: Caller) -> str | None:
        with relay.db.session() as db:
            return db.scalar(select(RelayAccount.id).where(RelayAccount.client_id == client.id))

    def _create_account(raw: Raw) -> dict[str, Any]:
        client = authenticate(relay, raw)
        body = _parse(AccountIn, raw.body)
        existing = _account_id(client)
        client_id, business_ref = client.id, client.business_ref
        with relay.stripe_http() as http:
            stripe = relay.stripe(http)
            try:
                if existing is not None:
                    return account_view(stripe.retrieve_account(existing))
                created = stripe.create_account(
                    country=body.country,
                    email=body.email,
                    name=body.name,
                    metadata={"tandem_relay_client": client_id, "tandem_business_ref": business_ref},
                    key=f"tandem-relay-account-{client_id}",
                )
            except StripeError as exc:
                raise _stripe_failure(exc) from None
        try:
            with relay.db.session() as db:
                db.add(RelayAccount(id=str(created["id"]), client_id=client_id, created_at=relay.clock()))
        except IntegrityError:
            pass  # a concurrent call stored it first; the idempotency key made it the same account
        log.info("relay.account_created", client=client_id, account=created.get("id"))
        return account_view(created)

    def _read_account(raw: Raw) -> dict[str, Any]:
        account_id = _account_id(authenticate(relay, raw))
        if account_id is None:
            raise RelayError(404, "no_account", "This business has no Stripe account yet")
        with relay.stripe_http() as http:
            try:
                return account_view(relay.stripe(http).retrieve_account(account_id))
            except StripeError as exc:
                raise _stripe_failure(exc) from None

    def _onboarding_link(raw: Raw) -> dict[str, Any]:
        client = authenticate(relay, raw)
        account_id, base = _account_id(client), client.base_url
        if account_id is None:
            raise RelayError(404, "no_account", "This business has no Stripe account yet")
        with relay.stripe_http() as http:
            try:
                link = relay.stripe(http).create_account_link(
                    account_id,
                    refresh_url=f"{base}{SETTINGS_PATH}?stripe=refresh",
                    return_url=f"{base}{SETTINGS_PATH}?stripe=return",
                )
            except StripeError as exc:
                raise _stripe_failure(exc) from None
        return {"url": link.get("url"), "expires_at": link.get("expires_at")}

    @app.post("/v1/account")
    async def create_account(request: Request) -> dict[str, Any]:
        return await run_in_threadpool(_create_account, await _raw(request))

    @app.get("/v1/account")
    async def read_account(request: Request) -> dict[str, Any]:
        return await run_in_threadpool(_read_account, await _raw(request))

    @app.post("/v1/account/onboarding_link")
    async def onboarding_link(request: Request) -> dict[str, Any]:
        return await run_in_threadpool(_onboarding_link, await _raw(request))

    # -- Checkout Sessions ------------------------------------------------------------------------------------

    def _require_account(client: Caller) -> str:
        account_id = _account_id(client)
        if account_id is None:
            raise RelayError(404, "no_account", "This business has no Stripe account yet")
        return account_id

    def _create_checkout(raw: Raw) -> dict[str, Any]:
        client = authenticate(relay, raw)
        body = _parse(CheckoutIn, raw.body)
        for path in (body.success_path, body.cancel_path):
            if not PAY_PATH.match(path):
                raise RelayError(422, "invalid_return_path", "Checkout may only return to the public pay page")
        ahead = body.expires_at - relay.clock()
        if not CHECKOUT_MIN_SECONDS <= ahead <= CHECKOUT_MAX_SECONDS:
            raise RelayError(422, "invalid_expiry", "expires_at must be 30 minutes to 24 hours ahead")
        account_id = _require_account(client)
        methods = ["card"]
        if body.bank_transfers and body.currency == "usd":
            methods.append("us_bank_account")
        fee = body.amount * settings.application_fee_bps // 10_000
        metadata = {
            "tandem_request": body.request_id,
            "tandem_invoice": body.invoice_ref,
            "tandem_business_ref": client.business_ref,
        }
        with relay.stripe_http() as http:
            try:
                session = relay.stripe(http).create_checkout_session(
                    account_id,
                    amount=body.amount,
                    currency=body.currency,
                    name=body.description,
                    reference=body.request_id,
                    metadata=metadata,
                    success_url=f"{client.base_url}{body.success_path}",
                    cancel_url=f"{client.base_url}{body.cancel_path}",
                    expires_at=body.expires_at,
                    methods=methods,
                    application_fee=fee,
                    key=f"tandem-checkout-{client.id}-{body.request_id}",
                )
            except StripeError as exc:
                raise _stripe_failure(exc) from None
        log.info("relay.checkout_created", client=client.id, session=session.get("id"))
        return checkout_view(session)

    def _session_call(raw: Raw, session_id: str, *, expire: bool) -> dict[str, Any]:
        client = authenticate(relay, raw)
        if not SESSION_ID.match(session_id):
            raise RelayError(404, "not_found", "No such checkout session")
        account_id = _require_account(client)
        with relay.stripe_http() as http:
            stripe = relay.stripe(http)
            try:
                if expire:
                    session = stripe.expire_checkout_session(account_id, session_id)
                else:
                    session = stripe.retrieve_checkout_session(account_id, session_id)
            except StripeError as exc:
                if exc.status == 404:
                    raise RelayError(404, "not_found", "No such checkout session") from None
                raise _stripe_failure(exc) from None
        return checkout_view(session)

    @app.post("/v1/checkout_sessions")
    async def create_checkout(request: Request) -> dict[str, Any]:
        return await run_in_threadpool(_create_checkout, await _raw(request))

    @app.get("/v1/checkout_sessions/{session_id}")
    async def read_checkout(session_id: str, request: Request) -> dict[str, Any]:
        raw = await _raw(request)
        return await run_in_threadpool(lambda: _session_call(raw, session_id, expire=False))

    @app.post("/v1/checkout_sessions/{session_id}/expire")
    async def expire_checkout(session_id: str, request: Request) -> dict[str, Any]:
        raw = await _raw(request)
        return await run_in_threadpool(lambda: _session_call(raw, session_id, expire=True))

    # -- refunds and disputes -----------------------------------------------------------------------------------

    def _create_refund(raw: Raw) -> dict[str, Any]:
        client = authenticate(relay, raw)
        body = _parse(RefundIn, raw.body)
        account_id = _require_account(client)
        metadata = {
            "tandem_refund": body.refund_ref,
            "tandem_invoice": body.invoice_ref,
            "tandem_business_ref": client.business_ref,
        }
        with relay.stripe_http() as http:
            try:
                refund = relay.stripe(http).create_refund(
                    account_id,
                    payment_intent=body.payment_intent,
                    amount=body.amount,
                    metadata=metadata,
                    # With a platform fee, the platform pays its share of the fee back too (ADR-022, phase 3).
                    refund_application_fee=settings.application_fee_bps > 0,
                    key=f"tandem-refund-{client.id}-{body.refund_ref}",
                )
            except StripeError as exc:
                raise _stripe_failure(exc) from None
        log.info("relay.refund_created", client=client.id, refund=refund.get("id"))
        return refund_view(refund)

    def _read_object(raw: Raw, kind: str, object_id: str) -> dict[str, Any]:
        client = authenticate(relay, raw)
        pattern, label = (REFUND_ID, "refund") if kind == "refund" else (DISPUTE_ID, "dispute")
        if not pattern.match(object_id):
            raise RelayError(404, "not_found", f"No such {label}")
        account_id = _require_account(client)
        with relay.stripe_http() as http:
            stripe = relay.stripe(http)
            try:
                if kind == "refund":
                    return refund_view(stripe.retrieve_refund(account_id, object_id))
                return dispute_view(stripe.retrieve_dispute(account_id, object_id))
            except StripeError as exc:
                if exc.status == 404:
                    raise RelayError(404, "not_found", f"No such {label}") from None
                raise _stripe_failure(exc) from None

    @app.post("/v1/refunds")
    async def create_refund(request: Request) -> dict[str, Any]:
        return await run_in_threadpool(_create_refund, await _raw(request))

    @app.get("/v1/refunds/{refund_id}")
    async def read_refund(refund_id: str, request: Request) -> dict[str, Any]:
        raw = await _raw(request)
        return await run_in_threadpool(lambda: _read_object(raw, "refund", refund_id))

    @app.get("/v1/disputes/{dispute_id}")
    async def read_dispute(dispute_id: str, request: Request) -> dict[str, Any]:
        raw = await _raw(request)
        return await run_in_threadpool(lambda: _read_object(raw, "dispute", dispute_id))

    # -- fees and payouts (phase 4) ------------------------------------------------------------------------------

    def _read_fee(raw: Raw, intent_id: str) -> dict[str, Any]:
        client = authenticate(relay, raw)
        if not INTENT_ID.match(intent_id):
            raise RelayError(404, "not_found", "No such payment")
        account_id = _require_account(client)
        with relay.stripe_http() as http:
            try:
                return payment_fee_view(relay.stripe(http).retrieve_payment_intent(account_id, intent_id))
            except StripeError as exc:
                if exc.status == 404:
                    raise RelayError(404, "not_found", "No such payment") from None
                raise _stripe_failure(exc) from None

    def _read_payout(raw: Raw, payout_id: str, *, summary: bool) -> dict[str, Any]:
        client = authenticate(relay, raw)
        if not PAYOUT_ID.match(payout_id):
            raise RelayError(404, "not_found", "No such payout")
        account_id = _require_account(client)
        with relay.stripe_http() as http:
            stripe = relay.stripe(http)
            try:
                payout = payout_view(stripe.retrieve_payout(account_id, payout_id))
                if not summary:
                    return payout
                totals = {"charges": 0, "refunds": 0, "disputes": 0, "other": 0, "fees": 0, "count": 0}
                if not payout["automatic"]:  # Stripe lists what a payout contains only for automatic payouts
                    return {"payout": payout, "available": False, "complete": False, **totals}
                after: str | None = None
                for _ in range(SUMMARY_PAGES):
                    page = stripe.list_payout_transactions(account_id, payout_id, after)
                    data = [t for t in page.get("data") or [] if isinstance(t, dict)]
                    for txn in data:
                        add_to_summary(totals, txn)
                    if not page.get("has_more") or not data:
                        return {"payout": payout, "available": True, "complete": True, **totals}
                    after = str(data[-1].get("id"))
                return {"payout": payout, "available": True, "complete": False, **totals}
            except StripeError as exc:
                if exc.status == 404:
                    raise RelayError(404, "not_found", "No such payout") from None
                raise _stripe_failure(exc) from None

    @app.get("/v1/payments/{intent_id}/fee")
    async def read_fee(intent_id: str, request: Request) -> dict[str, Any]:
        raw = await _raw(request)
        return await run_in_threadpool(lambda: _read_fee(raw, intent_id))

    @app.get("/v1/payouts/{payout_id}")
    async def read_payout(payout_id: str, request: Request) -> dict[str, Any]:
        raw = await _raw(request)
        return await run_in_threadpool(lambda: _read_payout(raw, payout_id, summary=False))

    @app.get("/v1/payouts/{payout_id}/summary")
    async def read_payout_summary(payout_id: str, request: Request) -> dict[str, Any]:
        raw = await _raw(request)
        return await run_in_threadpool(lambda: _read_payout(raw, payout_id, summary=True))

    # -- Stripe's Connect webhook ---------------------------------------------------------------------------

    def _webhook(raw: Raw) -> dict[str, str]:
        secret = settings.stripe_webhook_secret.get_secret_value()
        if not secret:
            raise RelayError(503, "webhook_not_configured", "RELAY_STRIPE_WEBHOOK_SECRET is unset")
        if not verify_webhook(raw.body, raw.headers.get("stripe-signature", ""), secret, relay.clock()):
            raise RelayError(400, "bad_signature", "Stripe signature does not match")
        try:
            event = json.loads(raw.body)
        except ValueError:
            raise RelayError(400, "bad_body", "Event body is not JSON") from None
        event_id, account_id = event.get("id"), event.get("account")
        if not isinstance(event_id, str) or not isinstance(account_id, str):
            return {"status": "ignored"}  # platform events: nothing to forward
        with relay.db.session() as db:
            account = db.get(RelayAccount, account_id)
            if account is None:
                log.info("relay.event_unknown_account", event_id=event_id, account=account_id)
                return {"status": "ignored"}
            if db.get(RelayEvent, event_id) is not None:
                return {"status": "duplicate"}
            client = db.get(RelayClient, account.client_id)
            assert client is not None
            db.add(
                RelayEvent(
                    id=event_id,
                    client_id=client.id,
                    account_id=account_id,
                    type=str(event.get("type", ""))[:80],
                    body=delivery.envelope(event, client.business_ref),
                    received_at=relay.clock(),
                    attempts=0,
                )
            )
        return {"status": "accepted"}

    @app.post("/v1/stripe/webhook")
    async def stripe_webhook(request: Request, background: BackgroundTasks) -> dict[str, str]:
        result = await run_in_threadpool(_webhook, await _raw(request))
        if result["status"] == "accepted":
            background.add_task(relay.deliver)
        return result

    return app
