"""What Tandem prepares on its own for online payments (M21 phase 5). Nothing here moves money or changes an invoice:
each result waits for a person.

- Money taken after an invoice was settled (a customer paying twice, say) gets a card refund proposal drafted for
  approval, unless the business turned `draft_refunds` off. Approving it pays the surplus back through the usual
  phase 3 path; rejecting it keeps the money.
- A refund made in the Stripe Dashboard is matched to a credit note's card refund of the same amount once the credit
  note exists, so Tandem does not offer to pay it back a second time (`refunds.match_dashboard`).
- Payouts and unmatched Dashboard refunds are offered to the owner's briefing (`briefing_facts`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform import clock, jobs
from app.platform.config import is_regulated
from app.platform.context import Context

from . import connection, refunds
from . import permissions as P
from .models import PaymentPayout, PaymentRequest
from .schemas import CardRefundIn

DRAFT_JOB = "payments.draft_surplus_refund"
MATCH_JOB = "payments.match_dashboard_refunds"
DRAFT_PERMISSIONS = refunds.JOB_PERMISSIONS | {P.REFUND_PROPOSE, "approvals.proposal.create"}


def enqueue_surplus_draft(db: Session, ctx: Context, request_id: uuid.UUID) -> None:
    jobs.enqueue(
        db,
        DRAFT_JOB,
        {"request_id": str(request_id)},
        business_id=ctx.business_id,
        dedupe_key=f"payments-surplus-draft:{request_id}",
    )


def draft_surplus_job(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    """Job handler: draft the card refund of one paid payment's surplus, once, if the business wants that."""
    ctx = replace(ctx, permissions=DRAFT_PERMISSIONS)
    request_id = uuid.UUID(str(payload["request_id"]))
    request = db.scalar(
        select(PaymentRequest).where(PaymentRequest.business_id == ctx.business_id, PaymentRequest.id == request_id)
    )
    if request is None or request.status != "paid" or request.surplus_minor <= 0 or is_regulated():
        return
    conn = connection.get(db, ctx)
    if conn is None or conn.stripe_account_id is None or conn.status == "disconnected" or not conn.draft_refunds:
        return
    if min(refunds.surplus_left(db, ctx, request), refunds.refundable(db, ctx, request)) <= 0:
        return  # already asked for, refunded in the Dashboard, or disputed
    refunds.propose(db, ctx, request.invoice_id, CardRefundIn(request_id=request.id, source="surplus"), drafted=True)


def on_document_changed(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    """A credit note changes its invoice: if a Dashboard refund on that invoice waits for one, try to match it."""
    if payload.get("document_type") != "invoice":
        return
    invoice_id = uuid.UUID(str(payload["document_id"]))
    if not refunds.unmatched_dashboard(db, ctx, invoice_id):
        return
    jobs.enqueue(
        db,
        MATCH_JOB,
        {"invoice_id": str(invoice_id)},
        business_id=ctx.business_id,
        dedupe_key=f"payments-match:{invoice_id}:{event_id}",
    )


def match_job(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    refunds.match_dashboard(db, refunds.job_context(ctx), uuid.UUID(str(payload["invoice_id"])))


# -- the briefing (read by ai through public.py) ------------------------------------------------------------------


@dataclass(frozen=True)
class BriefingPayout:
    amount_minor: int
    currency: str
    status: str
    arrival_on: date | None


@dataclass(frozen=True)
class PaymentsBriefing:
    arriving: list[BriefingPayout]  # on their way, arriving within two days
    arrived: list[BriefingPayout]  # reached the bank in the last day
    failed: list[BriefingPayout]  # did not reach the bank, in the last week
    dashboard_refunds_to_match: int  # refunds made in Stripe that no credit note accounts for yet


def briefing_facts(db: Session, ctx: Context) -> PaymentsBriefing | None:
    """Stripe payouts and loose ends worth a line in the owner's briefing. None when online payments are not set up."""
    ctx.require(P.CONNECTION_READ)
    conn = connection.get(db, ctx)
    if conn is None or conn.stripe_account_id is None or conn.status == "disconnected":
        return None
    now = clock.now()
    rows = list(
        db.scalars(
            select(PaymentPayout)
            .where(
                PaymentPayout.business_id == ctx.business_id,
                PaymentPayout.arrival_date >= now - timedelta(days=7),
                PaymentPayout.arrival_date <= now + timedelta(days=2),
            )
            .order_by(PaymentPayout.arrival_date, PaymentPayout.id)
            .limit(50)
        )
    )

    def view(row: PaymentPayout) -> BriefingPayout:
        arrival = row.arrival_date.date() if row.arrival_date else None
        return BriefingPayout(row.amount_minor, row.currency, row.status, arrival)

    recent = now - timedelta(days=1)
    return PaymentsBriefing(
        arriving=[view(r) for r in rows if r.status in ("pending", "in_transit")][:5],
        arrived=[view(r) for r in rows if r.status == "paid" and r.arrival_date and r.arrival_date >= recent][:5],
        failed=[view(r) for r in rows if r.status in ("failed", "canceled")][:5],
        dashboard_refunds_to_match=len(refunds.unmatched_dashboard(db, ctx)),
    )
