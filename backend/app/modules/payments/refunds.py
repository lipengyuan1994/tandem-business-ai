"""Card refunds (M21 phase 3): paying money back to the customer's card through Stripe, only after approval.

A refund starts in Tandem from something the invoice's own records say is owed back: a credit note's refund
(recorded in sales with method "card") or the surplus of an online payment taken after the invoice was settled.
Staff with `payments.refund.propose` ask for it; the amount comes from those records, never from the request. It
waits in the approvals queue as `payments.card_refund` until someone with `payments.refund.send` (owner or admin)
approves; only then does a job ask the relay for the Stripe refund, with the refund row's id as the idempotency key.

Refunds made in the Stripe Dashboard arrive as `refund.*` events. Tandem records them against the payment they came
from and tells the business, but never changes the invoice by itself (a credit note does that; phase 5 drafts one).

The sales ledger already counts a credit note's refund when the credit note is issued, so a card refund that fails
changes nothing there: the row shows the failure and the refund can be asked for again.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.notifications import public as notifications
from app.modules.sales import public as sales
from app.platform import audit, clock, jobs
from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, NotFound
from app.platform.money import money_text

from . import connection, provider
from . import permissions as P
from .models import LIVE_REFUND_STATUSES, PaymentDispute, PaymentRefund, PaymentRequest
from .relay import RefundView, RelayError
from .schemas import CardRefundIn, CardRefundOut, CardRefundPayload, CardRefundProposedOut, RefundOption

ACTION = "payments.card_refund"
SEND_JOB = "payments.send_refund"
CHECK_JOB = "payments.check_refunds"
EVENTS = frozenset({"refund.created", "refund.updated", "refund.failed", "charge.refund.updated"})
REFUND_ID = re.compile(r"^(re|pyr)_[A-Za-z0-9]{1,100}$")
JOB_PERMISSIONS = frozenset(
    {P.CONNECTION_READ, P.CONNECTION_MANAGE, P.REFUND_SEND, "org.business.read", "sales.invoice.mark_paid"}
)
# Stripe's refund statuses, as Tandem keeps them. `requires_action` (rare, some bank refunds) waits like pending.
STRIPE_STATUS = {
    "pending": "pending",
    "requires_action": "pending",
    "succeeded": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}


def job_context(ctx: Context) -> Context:
    return replace(ctx, permissions=JOB_PERMISSIONS)


def _link(invoice_id: uuid.UUID) -> str:
    return f"/sales/invoices/{invoice_id}"


# -- what may be paid back ----------------------------------------------------------------------------------------


def _live_total(db: Session, ctx: Context, request_id: uuid.UUID, *, source: str | None = None) -> int:
    stmt = select(func.coalesce(func.sum(PaymentRefund.amount_minor), 0)).where(
        PaymentRefund.business_id == ctx.business_id,
        PaymentRefund.request_id == request_id,
        PaymentRefund.status.in_(LIVE_REFUND_STATUSES),
    )
    if source is not None:
        stmt = stmt.where(PaymentRefund.source == source)
    return int(db.scalar(stmt) or 0)


def _disputed(db: Session, ctx: Context, request_id: uuid.UUID) -> bool:
    """Stripe refuses refunds on a disputed charge, and a lost dispute already took the money back."""
    found = db.scalar(
        select(PaymentDispute.id).where(
            PaymentDispute.business_id == ctx.business_id,
            PaymentDispute.request_id == request_id,
            PaymentDispute.outcome.in_(("open", "lost")),
        )
    )
    return found is not None


def refundable(db: Session, ctx: Context, request: PaymentRequest) -> int:
    """What is left of one paid card payment to pay back: the amount taken less live refunds (asked for, being
    sent, pending or done, from Tandem or the Dashboard). Nothing while the payment is disputed."""
    if request.status != "paid" or not request.payment_intent_id or _disputed(db, ctx, request.id):
        return 0
    return max(0, request.amount_minor - _live_total(db, ctx, request.id))


def surplus_left(db: Session, ctx: Context, request: PaymentRequest) -> int:
    return max(0, request.surplus_minor - _live_total(db, ctx, request.id, source="surplus"))


def _paid_requests(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[PaymentRequest]:
    return list(
        db.scalars(
            select(PaymentRequest)
            .where(
                PaymentRequest.business_id == ctx.business_id,
                PaymentRequest.invoice_id == invoice_id,
                PaymentRequest.status == "paid",
            )
            .order_by(PaymentRequest.paid_at, PaymentRequest.id)
        )
    )


def _credit_refunds_taken(db: Session, ctx: Context, invoice_id: uuid.UUID) -> set[uuid.UUID]:
    return {
        r
        for r in db.scalars(
            select(PaymentRefund.invoice_refund_id).where(
                PaymentRefund.business_id == ctx.business_id,
                PaymentRefund.invoice_id == invoice_id,
                PaymentRefund.invoice_refund_id.is_not(None),
                PaymentRefund.status.in_(LIVE_REFUND_STATUSES),
            )
        )
        if r is not None
    }


def options(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[RefundOption]:
    """Card refunds that may be asked for now. A credit note's card refund is offered from each payment that can
    still cover all of it; a payment's surplus only from that payment."""
    if not ctx.has(P.REFUND_PROPOSE) or is_regulated():
        return []
    requests = _paid_requests(db, ctx, invoice_id)
    if not requests:
        return []
    taken = _credit_refunds_taken(db, ctx, invoice_id)
    owed = [r for r in sales.online_refunds(db, ctx, invoice_id) if r.method == "card" and r.id not in taken]
    out: list[RefundOption] = []
    for request in requests:
        room = refundable(db, ctx, request)
        for refund in owed:
            if refund.amount_minor <= room:
                out.append(
                    RefundOption(
                        request_id=request.id,
                        source="credit_note",
                        invoice_refund_id=refund.id,
                        credit_note_number=refund.credit_note_number,
                        amount_minor=refund.amount_minor,
                        currency=request.currency,
                    )
                )
        surplus = min(surplus_left(db, ctx, request), room)
        if surplus > 0:
            out.append(
                RefundOption(request_id=request.id, source="surplus", amount_minor=surplus, currency=request.currency)
            )
    return out


def _credit_numbers(db: Session, ctx: Context, invoice_id: uuid.UUID) -> dict[uuid.UUID, str | None]:
    return {r.id: r.credit_note_number for r in sales.online_refunds(db, ctx, invoice_id)}


def out(row: PaymentRefund, numbers: dict[uuid.UUID, str | None] | None = None) -> CardRefundOut:
    return CardRefundOut(
        id=row.id,
        request_id=row.request_id,
        source=row.source,
        amount_minor=row.amount_minor,
        currency=row.currency,
        status=row.status,
        failure_reason=row.failure_reason,
        proposal_id=row.proposal_id,
        credit_note_number=(numbers or {}).get(row.invoice_refund_id) if row.invoice_refund_id else None,
        created_at=row.created_at,
        settled_at=row.settled_at,
    )


def for_invoice(db: Session, ctx: Context, invoice_id: uuid.UUID) -> list[CardRefundOut]:
    rows = list(
        db.scalars(
            select(PaymentRefund)
            .where(PaymentRefund.business_id == ctx.business_id, PaymentRefund.invoice_id == invoice_id)
            .order_by(PaymentRefund.created_at.desc(), PaymentRefund.id)
        )
    )
    numbers = _credit_numbers(db, ctx, invoice_id) if any(r.invoice_refund_id for r in rows) else {}
    return [out(r, numbers) for r in rows]


# -- asking for a refund -------------------------------------------------------------------------------------------


def propose(db: Session, ctx: Context, invoice_id: uuid.UUID, body: CardRefundIn) -> CardRefundProposedOut:
    ctx.require(P.REFUND_PROPOSE)
    if is_regulated():
        raise Conflict("payments.regulated_profile", detail="Online payments are off under this data profile")
    conn = connection.get(db, ctx)
    if conn is None or conn.stripe_account_id is None:
        raise Conflict("payments.not_connected", detail="Set up online payments first")
    request = db.scalar(
        select(PaymentRequest)
        .where(
            PaymentRequest.business_id == ctx.business_id,
            PaymentRequest.invoice_id == invoice_id,
            PaymentRequest.id == body.request_id,
        )
        .with_for_update()  # one refund decision per payment at a time
    )
    if request is None:
        raise NotFound("payments.request_not_found")
    invoice = sales.online_invoice(db, ctx, invoice_id)
    room = refundable(db, ctx, request)
    if body.source == "credit_note":
        if body.invoice_refund_id is None:
            raise Conflict("payments.refund_not_owed", detail="Say which credit note's refund to pay back")
        owed = next((r for r in sales.online_refunds(db, ctx, invoice_id) if r.id == body.invoice_refund_id), None)
        if owed is None or owed.method != "card":
            raise Conflict(
                "payments.refund_not_owed",
                detail="Only a credit note's refund recorded as paid back by card can be sent to the card",
            )
        if owed.id in _credit_refunds_taken(db, ctx, invoice_id):
            raise Conflict("payments.refund_exists", detail="This credit note's refund was already asked for")
        amount = owed.amount_minor
        what = f"credit note {owed.credit_note_number}"
    else:
        if body.invoice_refund_id is not None:
            raise Conflict("payments.refund_not_owed", detail="A surplus refund is not for a credit note")
        amount = min(surplus_left(db, ctx, request), room)
        what = "money taken after the invoice was settled"
    if amount <= 0 or amount > room:
        raise Conflict(
            "payments.refund_too_large",
            detail="This payment does not have that much left to pay back (refunded already, or disputed)",
            refundable_minor=room,
        )
    row = PaymentRefund(
        business_id=ctx.business_id,
        request_id=request.id,
        invoice_id=invoice_id,
        source=body.source,
        invoice_refund_id=body.invoice_refund_id,
        amount_minor=amount,
        currency=request.currency,
        status="proposed",
        requested_by=ctx.user_id,
    )
    db.add(row)
    db.flush()
    money = money_text(amount, request.currency)
    proposal = approvals.create_proposal(
        db,
        ctx,
        action_type=ACTION,
        payload=CardRefundPayload(refund_id=row.id).model_dump(mode="json"),
        title=f"Pay {money} back to the customer's card for invoice {invoice.number} ({what})",
    )
    row.proposal_id = proposal.id
    audit.record(
        db,
        ctx,
        action="payments.refund_proposed",
        target_type="payment_refund",
        target_id=row.id,
        summary=f"Asked to pay {money} back by card for invoice {invoice.number} ({what}); waiting for approval",
        proposal_id=proposal.id,
    )
    db.flush()
    numbers = _credit_numbers(db, ctx, invoice_id) if row.invoice_refund_id else {}
    return CardRefundProposedOut(refund=out(row, numbers), proposal_id=proposal.id)


# -- the approvals executor ---------------------------------------------------------------------------------------


def _get(db: Session, ctx: Context, refund_id: uuid.UUID, *, lock: bool = False) -> PaymentRefund:
    stmt = select(PaymentRefund).where(PaymentRefund.business_id == ctx.business_id, PaymentRefund.id == refund_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound("payments.refund_not_found")
    return row


def _request(db: Session, ctx: Context, request_id: uuid.UUID, *, lock: bool = False) -> PaymentRequest:
    stmt = select(PaymentRequest).where(PaymentRequest.business_id == ctx.business_id, PaymentRequest.id == request_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    assert row is not None
    return row


def describe(db: Session, ctx: Context, p: CardRefundPayload) -> list[PreviewLine]:
    row = _get(db, ctx, p.refund_id)
    request = _request(db, ctx, row.request_id)
    invoice = sales.online_invoice(db, ctx, row.invoice_id)
    if row.invoice_refund_id is not None:
        number = _credit_numbers(db, ctx, row.invoice_id).get(row.invoice_refund_id)
        why = f"Refund owed on credit note {number}"
    else:
        why = "Money taken after the invoice was already settled"
    paid = request.paid_at.date().isoformat() if request.paid_at else "-"
    return [
        PreviewLine("Invoice", None, invoice.number),
        PreviewLine("Customer", None, invoice.bill_to_name),
        PreviewLine("Pay back to card", None, money_text(row.amount_minor, row.currency)),
        PreviewLine("Why", None, why),
        PreviewLine("From the card payment of", None, f"{money_text(request.amount_minor, request.currency)}, {paid}"),
    ]


def _check_room(db: Session, ctx: Context, row: PaymentRefund) -> None:
    """The payment can still take this refund: paid, not disputed, and its live refunds (this one included) are
    no larger than what was taken."""
    request = _request(db, ctx, row.request_id)
    if request.status != "paid" or not request.payment_intent_id or _disputed(db, ctx, request.id):
        raise Conflict("payments.refund_too_large", detail="This payment cannot be refunded now (disputed?)")
    if _live_total(db, ctx, request.id) > request.amount_minor:
        raise Conflict("payments.refund_too_large", detail="This payment no longer has that much left to pay back")


def validate(db: Session, ctx: Context, p: CardRefundPayload) -> None:
    row = _get(db, ctx, p.refund_id)
    if row.status != "proposed":
        raise Conflict("payments.refund_not_proposed", detail="This refund is no longer waiting for approval")
    _check_room(db, ctx, row)


def run(db: Session, ctx: Context, p: CardRefundPayload, meta: ExecutionMeta) -> dict[str, Any]:
    row = _get(db, ctx, p.refund_id, lock=True)
    if row.status != "proposed" or row.proposal_id != meta.proposal_id:
        raise Conflict("payments.refund_not_proposed", detail="This refund is no longer waiting for approval")
    _request(db, ctx, row.request_id, lock=True)
    _check_room(db, ctx, row)
    row.status, row.sent_at = "sending", clock.now()
    row.version += 1
    jobs.enqueue(
        db,
        SEND_JOB,
        {"refund_id": str(row.id)},
        business_id=ctx.business_id,
        dedupe_key=f"payments-refund:{ctx.business_id}:{row.id}",
    )
    audit.record(
        db,
        ctx,
        action="payments.refund_approved",
        target_type="payment_refund",
        target_id=row.id,
        summary=f"Approved paying {money_text(row.amount_minor, row.currency)} back to the customer's card",
        proposal_id=meta.proposal_id,
    )
    return {"refund_id": str(row.id)}


SPEC = ExecutorSpec(ACTION, CardRefundPayload, P.REFUND_SEND, describe, validate, run)


def on_proposal_closed(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    """A refund whose proposal was turned down, expired, was withdrawn or failed to run is closed, so it no
    longer holds the payment's refundable amount."""
    del event_id
    if payload.get("action_type") != ACTION or payload.get("outcome") == "succeeded":
        return
    row = db.scalar(
        select(PaymentRefund)
        .where(
            PaymentRefund.business_id == ctx.business_id,
            PaymentRefund.proposal_id == uuid.UUID(str(payload["proposal_id"])),
        )
        .with_for_update()
    )
    if row is None or row.status != "proposed":
        return
    row.status = "failed" if payload.get("outcome") == "failed" else "canceled"
    row.last_error = f"The approval was {payload.get('outcome')}"
    row.version += 1


# -- sending, and what Stripe says -------------------------------------------------------------------------------


def _notify(db: Session, ctx: Context, row: PaymentRefund, title: str, body: str) -> None:
    notifications.notify_permission(
        db, ctx, P.REFUND_SEND, kind="payments_refund", title=title, body=body, link=_link(row.invoice_id)
    )


def _apply_view(db: Session, ctx: Context, row: PaymentRefund, view: RefundView) -> None:
    status = STRIPE_STATUS.get(view.status, "pending")
    before = row.status
    row.stripe_refund_id = view.id
    row.status, row.failure_reason = status, (view.failure_reason or None) if status == "failed" else None
    if status in ("succeeded", "failed", "canceled") and row.settled_at is None:
        row.settled_at = clock.now()
    row.last_error = None
    row.version += 1
    if status == before:
        return
    money = money_text(row.amount_minor, row.currency)
    if status == "succeeded":
        audit.record(
            db,
            ctx,
            action="payments.refund_succeeded",
            target_type="payment_refund",
            target_id=row.id,
            summary=f"Stripe paid {money} back to the customer's card",
        )
    elif status in ("failed", "canceled"):
        reason = f" ({view.failure_reason})" if view.failure_reason else ""
        audit.record(
            db,
            ctx,
            action="payments.refund_failed",
            target_type="payment_refund",
            target_id=row.id,
            summary=f"Stripe could not pay {money} back to the customer's card{reason}",
        )
        _notify(
            db,
            ctx,
            row,
            f"A card refund of {money} did not go through",
            "Stripe could not pay it back. Ask for it again, or pay the customer another way.",
        )


def _fail(ctx: Context, refund_id: uuid.UUID, message: str) -> None:
    with transaction(ctx) as db:
        row = _get(db, ctx, refund_id, lock=True)
        if row.status != "sending":
            return
        row.status, row.settled_at, row.last_error = "failed", clock.now(), message[:500]
        row.version += 1
        money = money_text(row.amount_minor, row.currency)
        audit.record(
            db,
            ctx,
            action="payments.refund_failed",
            target_type="payment_refund",
            target_id=row.id,
            summary=f"Stripe refused to pay {money} back to the customer's card: {message}"[:500],
        )
        _notify(
            db,
            ctx,
            row,
            f"A card refund of {money} was refused",
            "Stripe refused it. Ask for it again, or pay the customer another way.",
        )


def send_job(ctx: Context, payload: dict[str, Any]) -> None:
    """Job handler (non-transactional). Safe to run again: the refund id is Stripe's idempotency key."""
    ctx = job_context(ctx)
    refund_id = uuid.UUID(str(payload["refund_id"]))
    with transaction(ctx) as db:
        row = _get(db, ctx, refund_id)
        if row.status != "sending":
            return
        request = _request(db, ctx, row.request_id)
        conn = connection.get(db, ctx)
        creds = connection.credentials(conn) if conn is not None else None
        intent, invoice_id, amount = request.payment_intent_id, row.invoice_id, row.amount_minor
    if creds is None or intent is None:
        _fail(ctx, refund_id, "Online payments are not set up any more")
        return
    with provider.http_client() as client:
        try:
            view = provider.api(client).create_refund(
                creds, refund_id=str(refund_id), invoice_id=str(invoice_id), payment_intent=intent, amount=amount
            )
        except RelayError as exc:
            if exc.kind == "unavailable":
                with transaction(ctx) as db:
                    row = _get(db, ctx, refund_id, lock=True)
                    row.last_error, row.version = exc.message[:500], row.version + 1
                raise connection.failure(exc) from None  # the job runner retries with back-off
            _fail(ctx, refund_id, exc.message)
            return
    with transaction(ctx) as db:
        row = _get(db, ctx, refund_id, lock=True)
        if row.stripe_refund_id is None or row.stripe_refund_id == view.id:
            _apply_view(db, ctx, row, view)


def _read(ctx: Context, refund_id: str) -> RefundView | None:
    with transaction(ctx) as db:
        conn = connection.get(db, ctx)
        if conn is None:
            return None
        creds = connection.credentials(conn)
    with provider.http_client() as client:
        try:
            return provider.api(client).get_refund(creds, refund_id)
        except RelayError as exc:
            if exc.kind == "not_found":
                return None
            raise connection.failure(exc) from None


def apply(ctx: Context, refund_id: str) -> str:
    """Bring Tandem's record of one Stripe refund up to date. A refund Tandem did not send (made in the Dashboard)
    is recorded against the payment it came from, and the business is told; the invoice is left as it is."""
    ctx = job_context(ctx)
    if not REFUND_ID.match(refund_id):
        return "ignored"
    view = _read(ctx, refund_id)
    if view is None:
        return "ignored"
    with transaction(ctx) as db:
        row = db.scalar(
            select(PaymentRefund)
            .where(PaymentRefund.business_id == ctx.business_id, PaymentRefund.stripe_refund_id == view.id)
            .with_for_update()
        )
        if row is None and view.refund_ref:
            try:
                ours = uuid.UUID(view.refund_ref)
            except ValueError:
                ours = None
            if ours is not None:
                row = db.scalar(
                    select(PaymentRefund)
                    .where(
                        PaymentRefund.business_id == ctx.business_id,
                        PaymentRefund.id == ours,
                        PaymentRefund.stripe_refund_id.is_(None),
                    )
                    .with_for_update()
                )
        if row is None:
            return _dashboard(db, ctx, view)
        if row.status in ("proposed", "canceled") and row.stripe_refund_id is None:
            return "ignored"  # not sent by Tandem: a refund id in metadata cannot approve a refund
        _apply_view(db, ctx, row, view)
        return "applied"


def _dashboard(db: Session, ctx: Context, view: RefundView) -> str:
    if not view.payment_intent:
        return "ignored"
    request = db.scalar(
        select(PaymentRequest).where(
            PaymentRequest.business_id == ctx.business_id,
            PaymentRequest.payment_intent_id == view.payment_intent,
            PaymentRequest.status == "paid",
        )
    )
    if request is None or view.currency.upper() != request.currency.upper() or view.amount <= 0:
        return "ignored"
    status = STRIPE_STATUS.get(view.status, "pending")
    row_id = db.scalar(
        pg_insert(PaymentRefund)
        .values(
            id=uuid.uuid4(),
            business_id=ctx.business_id,
            request_id=request.id,
            invoice_id=request.invoice_id,
            source="dashboard",
            amount_minor=view.amount,
            currency=request.currency,
            status=status,
            stripe_refund_id=view.id,
            failure_reason=view.failure_reason if status == "failed" else None,
            settled_at=clock.now() if status in ("succeeded", "failed", "canceled") else None,
        )
        .on_conflict_do_nothing(index_elements=["stripe_refund_id"])
        .returning(PaymentRefund.id)
    )
    if row_id is None:
        return "applied"  # another worker recorded it first
    invoice = sales.online_invoice(db, ctx, request.invoice_id)
    money = money_text(view.amount, request.currency)
    audit.record(
        db,
        ctx,
        action="payments.dashboard_refund",
        target_type="payment_refund",
        target_id=row_id,
        summary=f"A refund of {money} for invoice {invoice.number} was made in Stripe's Dashboard; invoice unchanged",
    )
    notifications.notify_permission(
        db,
        ctx,
        P.REFUND_SEND,
        kind="payments_refund",
        title=f"A {money} refund for invoice {invoice.number} was made in Stripe",
        body="Tandem has not changed the invoice. Issue a credit note if the customer is owed this money.",
        link=_link(request.invoice_id),
    )
    return "applied"


def check_job(ctx: Context, payload: dict[str, Any]) -> None:
    """Hourly: send approved refunds whose job gave up, and re-read refunds Stripe has not finished."""
    del payload
    ctx = job_context(ctx)
    now = clock.now()
    with transaction(ctx) as db:
        rows = list(
            db.scalars(
                select(PaymentRefund).where(
                    PaymentRefund.business_id == ctx.business_id, PaymentRefund.status.in_(("sending", "pending"))
                )
            )
        )
        late = now - timedelta(minutes=15)
        stuck = [r.id for r in rows if r.status == "sending" and (r.sent_at or r.updated_at) < late]
        pending = [r.stripe_refund_id for r in rows if r.status == "pending" and r.stripe_refund_id]
        for refund_id in stuck:
            jobs.enqueue(
                db,
                SEND_JOB,
                {"refund_id": str(refund_id)},
                business_id=ctx.business_id,
                dedupe_key=f"payments-refund:{ctx.business_id}:{refund_id}:{int(now.timestamp()) // 3600}",
            )
    for stripe_id in pending:
        assert stripe_id is not None
        apply(ctx, stripe_id)
