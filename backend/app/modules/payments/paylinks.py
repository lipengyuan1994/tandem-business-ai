"""Invoice pay links, the public pay page and Stripe Checkout payment requests (M21 phase 2).

A pay link is a signed capability for one invoice (tokens.py). Opening it shows the invoice's open balance;
paying starts one Stripe Checkout Session for exactly that balance, as a direct charge on the business's own
Stripe account. Card details are entered only on Stripe's page. Money reaches the invoice only when Stripe says
the session is paid, read back through the relay (never from the event alone), and each payment is recorded once.

Network calls never run inside a transaction that holds a row lock: each step reads what it needs, talks to the
relay, then stores the result in a short transaction of its own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.modules.sales import public as sales
from app.platform import audit, clock, jobs
from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, NotFound
from app.platform.money import money_text

from . import connection, disputes, drafts, provider, refunds, tokens
from . import permissions as P
from .models import PaymentConnection, PaymentLink, PaymentRequest
from .relay import CheckoutView, Credentials, RelayError
from .schemas import (
    CheckoutStartOut,
    InvoiceOnlinePaymentsOut,
    PayLinkOut,
    PaymentRequestOut,
    PayPageOut,
    Unavailable,
)

CHECKOUT_LIFETIME = timedelta(hours=1)
REUSE_IF_LEFT = timedelta(minutes=10)
# A payment page still being opened. The relay call cannot outlast this (app.platform.http's timeouts add up to about
# 70 seconds), so an older `creating` request is one whose opener gave up, and does not block a new page.
STARTING_FOR = timedelta(minutes=2)
JOB_PERMISSIONS = frozenset(
    {P.CONNECTION_READ, P.CONNECTION_MANAGE, P.LINK_SHARE, "org.business.read", "sales.invoice.mark_paid"}
)
CLOSE_JOB = "payments.close_requests"
CHECK_JOB = "payments.check_requests"
FEE_JOB = "payments.read_fees"  # payouts.read_fees_job: Stripe's fee on a paid payment (phase 4)
COMPLETED = "checkout.session.completed"
ASYNC_SUCCEEDED = "checkout.session.async_payment_succeeded"
ASYNC_FAILED = "checkout.session.async_payment_failed"
EXPIRED = "checkout.session.expired"
CHECKOUT_EVENTS = frozenset({COMPLETED, ASYNC_SUCCEEDED, ASYNC_FAILED, EXPIRED})
LIVE = ("creating", "open", "processing")


def job_context(ctx: Context) -> Context:
    return replace(ctx, permissions=JOB_PERMISSIONS)


# -- availability ---------------------------------------------------------------------------------------------


def _unavailable(conn: PaymentConnection | None) -> Unavailable | None:
    if is_regulated():
        return "regulated_profile"
    if not provider.configured():
        return "not_configured"
    if conn is None or conn.stripe_account_id is None:
        return "not_connected"
    if conn.status != "active" or not conn.charges_enabled:
        return "not_active"
    return None


def _invoice_unavailable(invoice: sales.OnlineInvoice) -> Unavailable | None:
    if invoice.status == "paid" or (invoice.status == "issued" and invoice.balance_minor == 0):
        return "settled"
    if invoice.status != "issued":
        return "not_issued"
    return None


def _active_link(db: Session, ctx: Context, invoice_id: uuid.UUID) -> PaymentLink | None:
    return db.scalar(
        select(PaymentLink).where(
            PaymentLink.business_id == ctx.business_id,
            PaymentLink.invoice_id == invoice_id,
            PaymentLink.revoked_at.is_(None),
        )
    )


def _requests(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[PaymentRequest]:
    return list(
        db.scalars(
            select(PaymentRequest)
            .where(PaymentRequest.business_id == ctx.business_id, PaymentRequest.invoice_id == invoice_id)
            .order_by(PaymentRequest.created_at.desc(), PaymentRequest.id)
        )
    )


def _url(ctx: Context, link: PaymentLink) -> str:
    assert ctx.business_id is not None
    return provider.pay_url(tokens.make_token(ctx.business_id, link.invoice_id, link.id))


# -- staff: share a pay link ------------------------------------------------------------------------------------


def summary(db: Session, ctx: Context, invoice_id: uuid.UUID) -> InvoiceOnlinePaymentsOut:
    ctx.require(P.LINK_SHARE)
    invoice = sales.online_invoice(db, ctx, invoice_id)
    conn = connection.get(db, ctx)
    reason = _unavailable(conn) or _invoice_unavailable(invoice)
    link = _active_link(db, ctx, invoice_id)
    return InvoiceOnlinePaymentsOut(
        invoice_id=invoice_id,
        available=reason is None,
        unavailable_reason=reason,
        link_url=_url(ctx, link) if link is not None else None,
        link_created_at=link.created_at if link else None,
        link_last_opened_at=link.last_opened_at if link else None,
        mode="live" if conn is not None and conn.mode == "live" else ("test" if conn is not None else None),
        requests=[
            PaymentRequestOut(
                id=r.id,
                status=r.status,
                amount_minor=r.amount_minor,
                currency=r.currency,
                method=r.method,
                applied_minor=r.applied_minor,
                surplus_minor=r.surplus_minor,
                created_at=r.created_at,
                paid_at=r.paid_at,
                stripe_fee_minor=r.stripe_fee_minor,
                net_minor=r.net_minor,
            )
            for r in _requests(db, ctx, invoice_id)
            if r.status != "creating"
        ],
        refunds=refunds.for_invoice(db, ctx, invoice_id),
        refund_options=refunds.options(db, ctx, invoice_id),
        disputes=disputes.for_invoice(db, ctx, invoice_id),
    )


def share(db: Session, ctx: Context, invoice_id: uuid.UUID, *, reset: bool = False) -> PayLinkOut:
    """The invoice's pay link, made on first use. `reset` revokes the current link (if it leaked) and makes another."""
    ctx.require(P.LINK_SHARE)
    invoice = sales.online_invoice(db, ctx, invoice_id)
    reason = _unavailable(connection.get(db, ctx)) or _invoice_unavailable(invoice)
    if reason is not None:
        raise Conflict(f"payments.{reason}", detail="This invoice cannot be paid online right now")
    return PayLinkOut(url=_url(ctx, _ensure_link(db, ctx, invoice, reset=reset)))


def _ensure_link(db: Session, ctx: Context, invoice: sales.OnlineInvoice, *, reset: bool = False) -> PaymentLink:
    invoice_id = invoice.id
    link = _active_link(db, ctx, invoice_id)
    if link is not None and reset:
        link.revoked_at = clock.now()
        link.version += 1
        db.flush()
        audit.record(
            db,
            ctx,
            action="payments.link_reset",
            target_type="payment_link",
            target_id=link.id,
            summary=f"Replaced the pay link of invoice {invoice.number}; the old link no longer works",
        )
        link = None
    if link is None:
        assert ctx.business_id is not None
        link_id = uuid.uuid4()
        token = tokens.make_token(ctx.business_id, invoice_id, link_id)
        db.execute(
            pg_insert(PaymentLink)
            .values(
                id=link_id,
                business_id=ctx.business_id,
                invoice_id=invoice_id,
                token_hash=tokens.token_hash(token),
                created_by=ctx.user_id,
            )
            .on_conflict_do_nothing()
        )
        link = _active_link(db, ctx, invoice_id)  # a concurrent share may have made it first
        assert link is not None
        if link.id == link_id:
            audit.record(
                db,
                ctx,
                action="payments.link_created",
                target_type="payment_link",
                target_id=link.id,
                summary=f"Made a pay link for invoice {invoice.number}",
            )
    return link


REMINDER_SENDER = "sales.document.write"  # who may send a payment reminder (sales' own permission)


def reminder_link(db: Session, ctx: Context, invoice_id: uuid.UUID, make: bool) -> str | None:
    """The pay link a payment reminder carries (M21 phase 5; offered to sales through the registry). None when the
    business turned this off or the invoice cannot be paid online. Whoever may send the reminder may send its link.
    With `make` False nothing is written: the existing link, or "" when one would be made at sending."""
    if not ctx.has(REMINDER_SENDER):
        return None
    conn = connection.get(db, ctx)
    if conn is None or not conn.reminder_links or _unavailable(conn) is not None:
        return None
    invoice = sales.online_invoice(db, ctx, invoice_id)
    if _invoice_unavailable(invoice) is not None:
        return None
    if not make:
        link = _active_link(db, ctx, invoice_id)
        return _url(ctx, link) if link is not None else ""
    return _url(ctx, _ensure_link(db, ctx, invoice))


# -- the customer: pay page and checkout ------------------------------------------------------------------------


def visitor_link(db: Session, ctx: Context, token: str, *, lock: bool = False) -> PaymentLink:
    business_id, invoice_id, link_id = tokens.read_token(token)
    stmt = select(PaymentLink).where(PaymentLink.business_id == business_id, PaymentLink.id == link_id)
    link = db.scalar(stmt.with_for_update() if lock else stmt)
    if (
        link is None
        or link.invoice_id != invoice_id
        or link.revoked_at is not None
        or link.token_hash != tokens.token_hash(token)
        or ctx.business_id != business_id
    ):
        raise NotFound("payments.link_not_found")
    return link


def _state(db: Session, ctx: Context, invoice: sales.OnlineInvoice) -> str:
    if invoice.status == "paid" or (invoice.status == "issued" and invoice.balance_minor == 0):
        return "paid"
    if invoice.status != "issued" or _unavailable(connection.get(db, ctx)) is not None:
        return "unavailable"
    processing = db.scalar(
        select(PaymentRequest.id).where(
            PaymentRequest.business_id == ctx.business_id,
            PaymentRequest.invoice_id == invoice.id,
            PaymentRequest.status == "processing",
        )
    )
    return "processing" if processing is not None else "payable"


def page(db: Session, ctx: Context, token: str) -> PayPageOut:
    link = visitor_link(db, ctx, token, lock=True)
    now = clock.now()
    if link.last_opened_at is None or now - link.last_opened_at > timedelta(minutes=5):
        link.last_opened_at = now
        link.version += 1
    invoice = sales.online_invoice(db, ctx, link.invoice_id)
    conn = connection.get(db, ctx)
    return PayPageOut(
        business_name=org.get_business_brief(db, ctx).name,
        invoice_number=invoice.number,
        currency=invoice.currency,
        total_minor=invoice.total_minor,
        balance_minor=invoice.balance_minor,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        state=_state(db, ctx, invoice),
        test_mode=conn is not None and conn.mode == "test",
    )


@dataclass(frozen=True, slots=True)
class _Start:
    reuse_url: str | None
    request_id: uuid.UUID | None
    invoice_id: uuid.UUID
    number: str | None
    amount: int
    currency: str
    expires_at: int
    creds: Credentials | None


def _prepare_checkout(ctx: Context, token: str) -> _Start:
    with transaction(ctx) as db:
        link = visitor_link(db, ctx, token, lock=True)
        # The invoice row lock serialises every start for this invoice (two tabs, two links), so the check below
        # sees a page another request is opening right now.
        invoice = sales.online_invoice(db, ctx, link.invoice_id, lock=True)
        state = _state(db, ctx, invoice)
        if state == "paid":
            raise Conflict("payments.invoice_settled", detail="This invoice is already paid")
        if state == "processing":
            raise Conflict("payments.payment_processing", detail="A bank payment for this invoice is on its way")
        if state != "payable":
            raise Conflict("payments.not_payable", detail="This invoice cannot be paid online right now")
        now = clock.now()
        for r in _requests(db, ctx, invoice.id):
            if (
                r.status == "open"
                and r.checkout_url
                and r.amount_minor == invoice.balance_minor
                and r.expires_at - now > REUSE_IF_LEFT
            ):
                return _Start(r.checkout_url, None, invoice.id, invoice.number, 0, invoice.currency, 0, None)
        if any(r.status == "creating" and now - _started(r) < STARTING_FOR for r in _requests(db, ctx, invoice.id)):
            # A second press, or a second tab: one Stripe page per invoice at a time, so the customer cannot pay twice.
            raise Conflict("payments.checkout_starting", detail="Stripe's payment page is opening; try again shortly")
        conn = connection.get(db, ctx)
        assert conn is not None
        creds = connection.credentials(conn)
        expires = now + CHECKOUT_LIFETIME
        row = PaymentRequest(
            business_id=ctx.business_id,
            link_id=link.id,
            invoice_id=invoice.id,
            status="creating",
            amount_minor=invoice.balance_minor,
            currency=invoice.currency.upper(),
            expires_at=expires,
        )
        db.add(row)
        db.flush()
        return _Start(
            None,
            row.id,
            invoice.id,
            invoice.number,
            invoice.balance_minor,
            invoice.currency,
            int(expires.timestamp()),
            creds,
        )


def _close_request(ctx: Context, request_id: uuid.UUID, status: str, error: str | None) -> None:
    with transaction(ctx) as db:
        row = db.get(PaymentRequest, request_id, with_for_update=True)
        if row is not None and row.status in LIVE:
            row.status, row.closed_at, row.version = status, clock.now(), row.version + 1
            row.last_error = error[:500] if error else None


def start_checkout(ctx: Context, token: str) -> CheckoutStartOut:
    start = _prepare_checkout(ctx, token)
    if start.reuse_url is not None:
        return CheckoutStartOut(url=start.reuse_url)
    assert start.request_id is not None and start.creds is not None
    with provider.http_client() as client:
        try:
            view = provider.api(client).create_checkout(
                start.creds,
                request_id=str(start.request_id),
                invoice_id=str(start.invoice_id),
                amount=start.amount,
                currency=start.currency,
                description=f"Invoice {start.number}" if start.number else "Invoice",
                success_path=f"{provider.PAY_PATH}{token}?paid=1",
                cancel_path=f"{provider.PAY_PATH}{token}",
                expires_at=start.expires_at,
                bank_transfers=provider.bank_transfers(),
            )
        except RelayError as exc:
            _close_request(ctx, start.request_id, "failed", exc.message)
            raise connection.failure(exc) from None
    if view.url is None or view.request_ref != str(start.request_id):
        _close_request(ctx, start.request_id, "failed", "The payments relay returned an unusable checkout session")
        raise Conflict("payments.relay_unavailable", detail="Stripe's payment page could not be opened")
    with transaction(ctx) as db:
        row = db.get(PaymentRequest, start.request_id, with_for_update=True)
        assert row is not None
        row.checkout_session_id, row.checkout_url, row.status = view.id, view.url, "open"
        row.version += 1
        # The invoice may have changed while Stripe opened the page (paid in cash, credited, voided). The lock makes
        # any change either finish first, so it is seen here, or wait until this request is open, so its own event
        # closes the page (on_document_changed).
        invoice = sales.online_invoice(db, ctx, start.invoice_id, lock=True)
        changed = invoice.status != "issued" or invoice.balance_minor != start.amount
        if changed:
            jobs.enqueue(
                db,
                CLOSE_JOB,
                {"invoice_id": str(start.invoice_id), "version": None},
                business_id=ctx.business_id,
                dedupe_key=f"payments-close:{ctx.business_id}:{start.invoice_id}:{row.id}",
            )
    if changed:
        raise Conflict("payments.invoice_changed", detail="This invoice changed while the payment page was opening")
    return CheckoutStartOut(url=view.url)


# -- applying what Stripe says ---------------------------------------------------------------------------------


def _read(ctx: Context, session_id: str) -> tuple[uuid.UUID, CheckoutView] | None:
    with transaction(ctx) as db:
        row = db.scalar(
            select(PaymentRequest).where(
                PaymentRequest.business_id == ctx.business_id, PaymentRequest.checkout_session_id == session_id
            )
        )
        if row is None or row.status in ("paid", "failed"):
            return None
        conn = connection.get(db, ctx)
        if conn is None:
            return None
        request_id, creds = row.id, connection.credentials(conn)
    with provider.http_client() as client:
        try:
            view = provider.api(client).get_checkout(creds, session_id)
        except RelayError as exc:
            raise connection.failure(exc) from None
    return request_id, view


def apply(ctx: Context, session_id: str, event_type: str | None = None) -> str:
    """Bring one payment request up to date with its Checkout Session. Returns what happened (for the event row)."""
    ctx = job_context(ctx)
    found = _read(ctx, session_id)
    if found is None:
        return "ignored"
    request_id, view = found
    with transaction(ctx) as db:
        row = db.get(PaymentRequest, request_id, with_for_update=True)
        assert row is not None
        row.last_checked_at = clock.now()
        row.version += 1
        if view.request_ref != str(row.id) or view.invoice_ref != str(row.invoice_id):
            row.last_error = "Stripe's session does not belong to this payment request"
            return "ignored"
        if row.status == "paid":
            return "applied"
        if view.status == "complete" and view.payment_status == "paid":
            if view.currency.upper() != row.currency.upper():
                row.last_error = f"Stripe charged {view.currency.upper()}, the invoice is in {row.currency}"
                return "ignored"
            _record_paid(db, ctx, row, view)
            return "applied"
        if view.status == "complete":
            if event_type == ASYNC_FAILED:
                row.status, row.closed_at = "failed", clock.now()
                row.last_error = "The customer's bank payment did not go through"
                audit.record(
                    db,
                    ctx,
                    action="payments.bank_payment_failed",
                    target_type="payment_request",
                    target_id=row.id,
                    summary="A customer's bank payment did not go through; the invoice is still open",
                )
            elif row.status != "processing":
                row.status, row.method = "processing", "bank_transfer"
            return "applied"
        if view.status == "expired" and row.status in LIVE:
            row.status, row.closed_at = "expired", clock.now()
        return "applied"


def _record_paid(db: Session, ctx: Context, row: PaymentRequest, view: CheckoutView) -> None:
    now = clock.now()
    method = "bank_transfer" if row.status == "processing" else "card"
    # Dated by when Stripe took the money: a payment heard of late (a delayed event, the hourly check) still lands
    # on the day the customer paid. Never in the future, whatever the clocks say.
    taken = connection.from_unix(view.paid_at)
    paid_at = min(taken, now) if taken is not None else now
    result = sales.record_online_payment(
        db,
        ctx,
        row.invoice_id,
        amount_minor=view.amount_total,
        paid_at=paid_at,
        provider="stripe",
        external_id=view.payment_intent or view.id,
        method=method,
    )
    row.status, row.method, row.paid_at, row.closed_at = "paid", method, paid_at, now
    row.payment_intent_id = view.payment_intent
    row.applied_minor, row.surplus_minor = result.applied_minor, result.surplus_minor
    row.invoice_payment_id = result.payment_id
    row.last_error = None
    jobs.enqueue(
        db, FEE_JOB, {"request_id": str(row.id)}, business_id=ctx.business_id, dedupe_key=f"payments-fee:{row.id}"
    )
    # Refunds or disputes of this payment that Stripe sent before it: apply them now.
    jobs.enqueue(
        db, refunds.REPLAY_JOB, {"request_id": str(row.id)}, business_id=ctx.business_id, dedupe_key=f"replay:{row.id}"
    )
    if result.surplus_minor:
        drafts.enqueue_surplus_draft(db, ctx, row.id)
    summary = f"Received {money_text(view.amount_total, row.currency)} online through Stripe"
    if result.surplus_minor:
        surplus = money_text(result.surplus_minor, row.currency)
        summary += f"; {surplus} could not go on the invoice (already settled): pay it back by card or keep it"
    audit.record(
        db,
        ctx,
        action="payments.payment_received",
        target_type="payment_request",
        target_id=row.id,
        summary=summary,
        changes={"applied_minor": result.applied_minor, "surplus_minor": result.surplus_minor},
    )


# -- keeping open sessions honest -------------------------------------------------------------------------------


def on_document_changed(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    """When an invoice's balance changes (paid by other means, credited, voided), open Stripe pages for the old
    balance are closed so the customer cannot pay twice. The relay call runs in a job, not in this transaction."""
    del event_id
    if payload.get("document_type") != "invoice":
        return
    invoice_id = uuid.UUID(str(payload["document_id"]))
    has_open = db.scalar(
        select(PaymentRequest.id).where(
            PaymentRequest.business_id == ctx.business_id,
            PaymentRequest.invoice_id == invoice_id,
            PaymentRequest.status == "open",
        )
    )
    if has_open is not None:
        jobs.enqueue(
            db,
            CLOSE_JOB,
            {"invoice_id": str(invoice_id), "version": payload.get("version")},
            business_id=ctx.business_id,
            dedupe_key=f"payments-close:{ctx.business_id}:{invoice_id}:{payload.get('version')}",
        )


def close_job(ctx: Context, payload: dict[str, Any]) -> None:
    ctx = job_context(ctx)
    invoice_id = uuid.UUID(str(payload["invoice_id"]))
    with transaction(ctx) as db:
        invoice = sales.online_invoice(db, ctx, invoice_id)
        stale = [
            (r.id, r.checkout_session_id)
            for r in _requests(db, ctx, invoice_id)
            if r.status == "open"
            and r.checkout_session_id
            and (invoice.status != "issued" or r.amount_minor != invoice.balance_minor)
        ]
        conn = connection.get(db, ctx)
        creds = connection.credentials(conn) if conn is not None and stale else None
    if creds is None:
        return
    with provider.http_client() as client:
        relay = provider.api(client)
        for request_id, session_id in stale:
            assert session_id is not None
            try:
                relay.expire_checkout(creds, session_id)
            except RelayError as exc:
                if exc.kind == "unavailable":
                    raise connection.failure(exc) from None  # the job runner retries
                # Stripe refused to expire it: it was paid or closed meanwhile. Read it back and apply.
                apply(ctx, session_id)
                continue
            _close_request(ctx, request_id, "expired", None)


def _started(row: PaymentRequest) -> datetime:
    return row.expires_at - CHECKOUT_LIFETIME


def check_job(ctx: Context, payload: dict[str, Any]) -> None:
    """Hourly: catch anything a lost or delayed webhook missed. Only requests that should have moved are read."""
    del payload
    ctx = job_context(ctx)
    now = clock.now()
    with transaction(ctx) as db:
        rows = list(
            db.scalars(
                select(PaymentRequest).where(
                    PaymentRequest.business_id == ctx.business_id, PaymentRequest.status.in_(LIVE)
                )
            )
        )
        # A request's start is its expiry less the page's lifetime (both from the app clock).
        abandoned = [r.id for r in rows if r.status == "creating" and now - _started(r) > timedelta(minutes=15)]
        due = [
            r.checkout_session_id
            for r in rows
            if r.checkout_session_id
            and r.status in ("open", "processing")
            and (r.last_checked_at is None or now - r.last_checked_at > timedelta(minutes=50))
            and (r.status == "processing" or now - _started(r) > timedelta(minutes=15))
        ]
    for request_id in abandoned:  # the relay never answered, so the customer never saw a Stripe page
        _close_request(ctx, request_id, "failed", "Stripe's payment page was never opened")
    for session_id in due:
        assert session_id is not None
        apply(ctx, session_id)
