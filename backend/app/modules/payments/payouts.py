"""Stripe fees and payouts (M21 phase 4).

Stripe takes its processing fee out of each payment and later pays the rest out to the business's bank in a payout,
together with whatever refunds and disputes took back meanwhile. Tandem reads each paid payment's fee once (so the
invoice shows what reached the business), and keeps one row per payout with Stripe's own totals of what the payout
contained, so the books can move the money from the Stripe clearing account to the bank and record Stripe's fees
(accounting reads `ledger_payouts`). Every event re-reads the payout through the relay; an hourly check catches lost
events and fills in fees Stripe had not settled yet. Tandem never starts, changes or cancels a payout.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.notifications import public as notifications
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.money import money_text

from . import connection, provider, refunds
from . import permissions as P
from .models import PAYOUT_STATUSES, PaymentPayout, PaymentRequest
from .relay import Credentials, PayoutView, RelayError
from .schemas import PayoutOut

EVENTS = frozenset(
    {
        "payout.created",
        "payout.updated",
        "payout.paid",
        "payout.failed",
        "payout.canceled",
        "payout.reconciliation_completed",
    }
)
PAYOUT_ID = re.compile(r"^po_[A-Za-z0-9]{1,100}$")
FEE_JOB = "payments.read_fees"
CHECK_JOB = "payments.check_payouts"
FEE_BATCH = 50
RECHECK_PAID_FOR = timedelta(days=7)  # Stripe can report a paid payout as failed several business days later
OPEN = ("pending", "in_transit")
STATUSES = frozenset(PAYOUT_STATUSES)
LIST_LIMIT = 100


# -- reading ------------------------------------------------------------------------------------------------------


def out(row: PaymentPayout) -> PayoutOut:
    return PayoutOut(
        id=row.id,
        amount_minor=row.amount_minor,
        currency=row.currency,
        status=row.status,
        arrival_date=row.arrival_date.date() if row.arrival_date else None,
        automatic=row.automatic,
        method=row.method,
        failure_code=row.failure_code,
        charges_minor=row.charges_minor,
        refunds_minor=row.refunds_minor,
        disputes_minor=row.disputes_minor,
        other_minor=row.other_minor,
        fees_minor=row.fees_minor,
        transaction_count=row.transaction_count,
        summarized=row.summarized_at is not None,
        summary_complete=row.summary_complete,
    )


def recent(db: Session, ctx: Context) -> list[PayoutOut]:
    ctx.require(P.CONNECTION_READ)
    rows = db.scalars(
        select(PaymentPayout)
        .where(PaymentPayout.business_id == ctx.business_id)
        .order_by(PaymentPayout.arrival_date.desc().nulls_first(), PaymentPayout.first_seen_at.desc())
        .limit(LIST_LIMIT)
    )
    return [out(r) for r in rows]


def _creds(ctx: Context) -> Credentials | None:
    with transaction(ctx) as db:
        conn = connection.get(db, ctx)
        if conn is None or not conn.relay_client_id or not conn.relay_secret_sealed:
            return None
        return connection.credentials(conn)


# -- fees on paid payments ----------------------------------------------------------------------------------------


def read_fees_job(ctx: Context, payload: dict[str, Any]) -> None:
    """Job handler: fill in Stripe's fee for one paid payment (`request_id`), or for every paid payment still
    without one (from the hourly check). Stripe settles some fees later (bank debits), so a missing one is retried."""
    ctx = refunds.job_context(ctx)
    with transaction(ctx) as db:
        query = select(PaymentRequest.id, PaymentRequest.payment_intent_id).where(
            PaymentRequest.business_id == ctx.business_id,
            PaymentRequest.status == "paid",
            PaymentRequest.stripe_fee_minor.is_(None),
            PaymentRequest.payment_intent_id.is_not(None),
        )
        if payload.get("request_id"):
            query = query.where(PaymentRequest.id == uuid.UUID(str(payload["request_id"])))
        # Least recently tried first, and each try is stamped, so payments whose fee Stripe never settles (or
        # refuses to read) take their turn instead of filling every batch.
        query = query.order_by(PaymentRequest.last_checked_at.asc().nulls_first(), PaymentRequest.paid_at)
        due = [(r.id, r.payment_intent_id) for r in db.execute(query.limit(FEE_BATCH))]
        if due:
            db.execute(
                update(PaymentRequest)
                .where(PaymentRequest.id.in_([r for r, _ in due]))
                .values(last_checked_at=clock.now(), version=PaymentRequest.version + 1)
            )
    if not due:
        return
    creds = _creds(ctx)
    if creds is None:
        return
    with provider.http_client() as client:
        relay = provider.api(client)
        for request_id, intent in due:
            assert intent is not None
            try:
                view = relay.get_payment_fee(creds, intent)
            except RelayError as exc:
                if exc.kind == "unavailable":
                    raise connection.failure(exc) from None  # the job runner retries
                continue
            if view.fee is None or view.net is None:
                continue  # not settled yet: the hourly check tries again
            with transaction(ctx) as db:
                row = db.get(PaymentRequest, request_id, with_for_update=True)
                if row is not None and row.stripe_fee_minor is None:
                    row.stripe_fee_minor, row.net_minor = view.fee, view.net
                    row.version += 1


# -- payouts ------------------------------------------------------------------------------------------------------


def _read(ctx: Context, creds: Credentials, payout_id: str) -> PayoutView | None:
    with provider.http_client() as client:
        try:
            return provider.api(client).get_payout(creds, payout_id)
        except RelayError as exc:
            if exc.kind == "not_found":
                return None
            raise connection.failure(exc) from None


def _notify_failed(db: Session, ctx: Context, row: PaymentPayout) -> None:
    money = money_text(row.amount_minor, row.currency)
    notifications.notify_permission(
        db,
        ctx,
        P.CONNECTION_MANAGE,
        kind="payments_payout",
        title=f"A {money} Stripe payout to your bank did not go through",
        body="Stripe put the money back in your Stripe balance. Check your bank details in your Stripe Dashboard.",
        link="/settings/payments",
    )


def apply(ctx: Context, payout_id: str) -> str:
    """Bring one payout up to date with Stripe. Returns what happened (for the event row)."""
    ctx = refunds.job_context(ctx)
    if not PAYOUT_ID.match(payout_id):
        return "ignored"
    creds = _creds(ctx)
    if creds is None:
        return "ignored"
    view = _read(ctx, creds, payout_id)
    if view is None or not view.currency:
        return "ignored"
    now = clock.now()
    with transaction(ctx) as db:
        row = db.scalar(
            select(PaymentPayout)
            .where(PaymentPayout.business_id == ctx.business_id, PaymentPayout.stripe_payout_id == view.id)
            .with_for_update()
        )
        before = row.status if row is not None else None
        seen = _fields(row) if row is not None else None
        if row is None:
            row = PaymentPayout(
                business_id=ctx.business_id,
                stripe_payout_id=view.id,
                amount_minor=view.amount,
                currency=view.currency.upper()[:3],
                status=view.status if view.status in STATUSES else "pending",
                automatic=view.automatic,
                first_seen_at=now,
            )
            db.add(row)
            db.flush()
        if view.status not in STATUSES:
            row.last_error = f"Stripe reported an unknown payout status: {view.status[:40]}"
        else:
            row.status = view.status
        row.amount_minor, row.automatic = view.amount, view.automatic
        row.arrival_date = connection.from_unix(view.arrival_date)
        row.method = (view.method or None) and view.method[:16]
        row.failure_code = (view.failure_code or None) and view.failure_code[:80]
        if seen is None or _fields(row) != seen:  # a re-read that changes nothing is not a new version for the books
            row.version += 1
        db.flush()
        if row.status != before and row.status in ("paid", "failed", "canceled"):
            money = money_text(row.amount_minor, row.currency)
            words = {"paid": "reached", "failed": "did not reach", "canceled": "was canceled before reaching"}
            audit.record(
                db,
                ctx,
                action=f"payments.payout_{row.status}",
                target_type="payment_payout",
                target_id=row.id,
                summary=f"A {money} Stripe payout {words[row.status]} the business's bank",
            )
            if row.status == "failed":
                _notify_failed(db, ctx, row)
        summarize = row.status == "paid" and not row.summary_complete and (row.automatic or row.summarized_at is None)
    if summarize:
        _summarize(ctx, creds, payout_id)
    return "applied"


def _fields(row: PaymentPayout) -> tuple[object, ...]:
    return (row.status, row.amount_minor, row.automatic, row.arrival_date, row.method, row.failure_code, row.last_error)


def _summarize(ctx: Context, creds: Credentials, payout_id: str) -> None:
    """Stripe's totals for what one paid payout contained. A manual payout has none: it is marked summarised so
    the books know to leave its fees to the bookkeeper."""
    with provider.http_client() as client:
        try:
            summary = provider.api(client).get_payout_summary(creds, payout_id)
        except RelayError as exc:
            if exc.kind == "not_found":
                return
            raise connection.failure(exc) from None
    with transaction(ctx) as db:
        row = db.scalar(
            select(PaymentPayout)
            .where(PaymentPayout.business_id == ctx.business_id, PaymentPayout.stripe_payout_id == payout_id)
            .with_for_update()
        )
        if row is None:
            return
        row.charges_minor, row.refunds_minor, row.disputes_minor = summary.charges, summary.refunds, summary.disputes
        row.other_minor, row.fees_minor, row.transaction_count = summary.other, summary.fees, summary.count
        row.summary_complete = summary.available and summary.complete
        row.summarized_at = clock.now()
        row.last_error = (
            None
            if row.summary_complete or not summary.available
            else ("Stripe's list of what this payout contained was too long to read in full")
        )
        row.version += 1


def check_job(ctx: Context, payload: dict[str, Any]) -> None:
    """Hourly (`payments.check_payouts`): fill in missing fees, and re-read payouts still on their way, not yet
    summarised, or paid in the last few days (a bank can return a paid payout, and Stripe then marks it failed), in
    case an event was lost."""
    read_fees_job(ctx, {})
    ctx = refunds.job_context(ctx)
    recent = clock.now() - RECHECK_PAID_FOR
    with transaction(ctx) as db:
        due = list(
            db.scalars(
                select(PaymentPayout.stripe_payout_id).where(
                    PaymentPayout.business_id == ctx.business_id,
                    PaymentPayout.status.in_(OPEN)
                    | (
                        (PaymentPayout.status == "paid")
                        & (PaymentPayout.summarized_at.is_(None) | (PaymentPayout.arrival_date >= recent))
                    ),
                )
            )
        )
    for payout_id in due:
        apply(ctx, payout_id)


# -- the books (read by accounting through public.py) -------------------------------------------------------------


@dataclass(frozen=True)
class LedgerPayout:
    id: uuid.UUID
    version: int
    stripe_payout_id: str
    currency: str
    amount_minor: int
    status: str
    arrival_on: date | None
    automatic: bool
    summary_complete: bool
    fees_minor: int


def ledger_payout_versions(db: Session, ctx: Context, since: date) -> dict[uuid.UUID, int]:
    """Payouts arriving on or after `since` that have finished (paid, failed or canceled): id -> version."""
    ctx.require(P.CONNECTION_READ)
    rows = db.execute(
        select(PaymentPayout.id, PaymentPayout.version, PaymentPayout.arrival_date).where(
            PaymentPayout.business_id == ctx.business_id, PaymentPayout.status.not_in(OPEN)
        )
    )
    return {r.id: r.version for r in rows if r.arrival_date is not None and r.arrival_date.date() >= since}


def ledger_payouts(db: Session, ctx: Context, ids: set[uuid.UUID]) -> dict[uuid.UUID, LedgerPayout]:
    ctx.require(P.CONNECTION_READ)
    if not ids:
        return {}
    rows = db.scalars(
        select(PaymentPayout).where(PaymentPayout.business_id == ctx.business_id, PaymentPayout.id.in_(list(ids)))
    )
    return {
        r.id: LedgerPayout(
            id=r.id,
            version=r.version,
            stripe_payout_id=r.stripe_payout_id,
            currency=r.currency,
            amount_minor=r.amount_minor,
            status=r.status,
            arrival_on=r.arrival_date.date() if r.arrival_date else None,
            automatic=r.automatic,
            summary_complete=r.summary_complete,
            fees_minor=r.fees_minor,
        )
        for r in rows
    }
