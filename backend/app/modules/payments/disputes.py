"""Disputes (chargebacks) on card payments (M21 phase 3).

A dispute is answered in the business's own Stripe Dashboard; Tandem tracks it so the business sees it on the
invoice with its deadline and is told when it opens. While it is open nothing changes on the invoice (Stripe holds
the money). A lost dispute takes the money back off the invoice and reopens it, once; a won one (or an inquiry that
closed without a chargeback) only clears the flag. Every event re-reads the dispute through the relay.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.notifications import public as notifications
from app.modules.sales import public as sales
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.money import money_text

from . import connection, provider, refunds
from . import permissions as P
from .models import PaymentDispute, PaymentRequest
from .relay import DisputeView, RelayError
from .schemas import DisputeOut

EVENTS = frozenset(
    {
        "charge.dispute.created",
        "charge.dispute.updated",
        "charge.dispute.closed",
        "charge.dispute.funds_withdrawn",
        "charge.dispute.funds_reinstated",
    }
)
DISPUTE_ID = re.compile(r"^(dp|du)_[A-Za-z0-9]{1,100}$")
WON = frozenset({"won", "warning_closed", "prevented"})
LOST = frozenset({"lost"})


def outcome_for(stripe_status: str) -> str:
    if stripe_status in WON:
        return "won"
    if stripe_status in LOST:
        return "lost"
    return "open"


def out(row: PaymentDispute) -> DisputeOut:
    return DisputeOut(
        id=row.id,
        request_id=row.request_id,
        amount_minor=row.amount_minor,
        currency=row.currency,
        stripe_status=row.stripe_status,
        reason=row.reason,
        outcome=row.outcome,
        due_by=row.due_by,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        reversed_minor=row.reversed_minor,
        last_error=row.last_error,
    )


def for_invoice(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[DisputeOut]:
    rows = db.scalars(
        select(PaymentDispute)
        .where(PaymentDispute.business_id == ctx.business_id, PaymentDispute.invoice_id == invoice_id)
        .order_by(PaymentDispute.opened_at.desc(), PaymentDispute.id)
    )
    return [out(r) for r in rows]


def _read(ctx: Context, dispute_id: str) -> DisputeView | None:
    with transaction(ctx) as db:
        conn = connection.get(db, ctx)
        if conn is None:
            return None
        creds = connection.credentials(conn)
    with provider.http_client() as client:
        try:
            return provider.api(client).get_dispute(creds, dispute_id)
        except RelayError as exc:
            if exc.kind == "not_found":
                return None
            raise connection.failure(exc) from None


def _notify(db: Session, ctx: Context, invoice_id: uuid.UUID, title: str, body: str) -> None:
    notifications.notify_permission(
        db,
        ctx,
        P.CONNECTION_MANAGE,
        kind="payments_dispute",
        title=title,
        body=body,
        link=f"/sales/invoices/{invoice_id}",
    )


def apply(ctx: Context, dispute_id: str) -> str:
    ctx = refunds.job_context(ctx)
    if not DISPUTE_ID.match(dispute_id):
        return "ignored"
    view = _read(ctx, dispute_id)
    if view is None or not view.payment_intent or view.amount <= 0:
        return "ignored"
    with transaction(ctx) as db:
        request = db.scalar(
            select(PaymentRequest)
            .where(
                PaymentRequest.business_id == ctx.business_id,
                PaymentRequest.payment_intent_id == view.payment_intent,
                PaymentRequest.status == "paid",
            )
            .with_for_update()
        )
        if request is None or view.currency.upper() != request.currency.upper():
            return "ignored"
        row = db.scalar(
            select(PaymentDispute)
            .where(PaymentDispute.business_id == ctx.business_id, PaymentDispute.stripe_dispute_id == view.id)
            .with_for_update()
        )
        invoice = sales.online_invoice(db, ctx, request.invoice_id)
        money = money_text(view.amount, request.currency)
        if row is None:
            row = PaymentDispute(
                business_id=ctx.business_id,
                request_id=request.id,
                invoice_id=request.invoice_id,
                stripe_dispute_id=view.id,
                amount_minor=view.amount,
                currency=request.currency,
                stripe_status=view.status[:32],
                reason=(view.reason or None) and view.reason[:40],
                outcome="open",
                due_by=connection.from_unix(view.due_by),
                opened_at=clock.now(),
            )
            db.add(row)
            db.flush()
            audit.record(
                db,
                ctx,
                action="payments.dispute_opened",
                target_type="payment_dispute",
                target_id=row.id,
                summary=f"The customer's bank disputed {money} paid online for invoice {invoice.number}",
            )
            if outcome_for(view.status) == "open":
                _notify(
                    db,
                    ctx,
                    request.invoice_id,
                    f"A {money} card payment for invoice {invoice.number} is disputed",
                    "Answer it in your Stripe Dashboard before the deadline shown on the invoice.",
                )
        row.amount_minor = view.amount
        row.stripe_status = view.status[:32]
        row.reason = (view.reason or None) and view.reason[:40]
        row.due_by = connection.from_unix(view.due_by)
        row.version += 1
        _settle(db, ctx, row, request, outcome_for(view.status), invoice.number)
        return "applied"


def _settle(
    db: Session, ctx: Context, row: PaymentDispute, request: PaymentRequest, outcome: str, number: str | None
) -> None:
    if outcome == row.outcome or outcome == "open":
        if outcome == "lost" and row.reversed_minor == 0 and row.last_error:
            _reverse(db, ctx, row, request, number)  # an earlier reversal failed: try again
        return
    row.outcome, row.closed_at = outcome, clock.now()
    money = money_text(row.amount_minor, row.currency)
    if outcome == "won":
        audit.record(
            db,
            ctx,
            action="payments.dispute_won",
            target_type="payment_dispute",
            target_id=row.id,
            summary=f"The dispute over {money} for invoice {number} closed in the business's favour",
        )
        return
    _reverse(db, ctx, row, request, number)
    _notify(
        db,
        ctx,
        row.invoice_id,
        f"The dispute over {money} for invoice {number} was lost",
        "The bank kept the money, so the invoice is open again for what came off it.",
    )


def _reverse(db: Session, ctx: Context, row: PaymentDispute, request: PaymentRequest, number: str | None) -> None:
    try:
        with db.begin_nested():
            taken = sales.reverse_online_payment(
                db,
                ctx,
                request.invoice_id,
                provider="stripe",
                external_id=request.payment_intent_id or request.checkout_session_id or "",
                amount_minor=row.amount_minor,
                reason="dispute_lost",
            )
    except DomainError as exc:
        row.last_error = (exc.detail or exc.code)[:500]
        return
    row.reversed_minor, row.last_error = taken, None
    audit.record(
        db,
        ctx,
        action="payments.dispute_lost",
        target_type="payment_dispute",
        target_id=row.id,
        summary=(
            f"Lost the dispute over {money_text(row.amount_minor, row.currency)} for invoice {number}; "
            f"{money_text(taken, row.currency)} came off the invoice"
        ),
        changes={"reversed_minor": taken},
    )


def check_job(ctx: Context, payload: dict[str, Any]) -> None:
    """Hourly (`payments.check_refunds`): finish refunds and re-read open disputes, in case an event was lost."""
    refunds.check_job(ctx, payload)
    ctx = refunds.job_context(ctx)
    with transaction(ctx) as db:
        open_ids = list(
            db.scalars(
                select(PaymentDispute.stripe_dispute_id).where(
                    PaymentDispute.business_id == ctx.business_id,
                    (PaymentDispute.outcome == "open")
                    | ((PaymentDispute.outcome == "lost") & (PaymentDispute.last_error.is_not(None))),
                )
            )
        )
    for dispute_id in open_ids:
        apply(ctx, dispute_id)
