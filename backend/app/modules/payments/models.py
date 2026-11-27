"""The business's link to Stripe through the payments relay, and every Stripe event the relay forwarded.

No card data is ever stored here: Stripe ids, readiness flags and what Stripe still asks for. The relay credential
(client id and shared secret) is stored sealed and never leaves this module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args

CONNECTION_STATUSES = ("onboarding", "active", "restricted", "disconnected")
EVENT_OUTCOMES = ("pending", "applied", "ignored", "failed")
# A payment request is one visit to Stripe Checkout. `creating` until the relay answers; `open` while the page can be
# paid; `processing` after a bank debit was started (the money arrives days later); `paid` once Stripe says so and
# the money is on the invoice; `expired` or `failed` otherwise.
REQUEST_STATUSES = ("creating", "open", "processing", "paid", "expired", "failed")
# A card refund (phase 3): `proposed` while it waits for approval; `sending` once approved, until Stripe answers;
# then Stripe's own `pending`, `succeeded` or `failed`; `canceled` if the proposal was turned down or expired, or
# Stripe canceled it.
REFUND_SOURCES = ("credit_note", "surplus", "dashboard")
CREDIT_NOTE_CHECK = (
    "(source = 'credit_note' and invoice_refund_id is not null)"
    " or (source = 'surplus' and invoice_refund_id is null) or source = 'dashboard'"
)
REFUND_STATUSES = ("proposed", "sending", "pending", "succeeded", "failed", "canceled")
LIVE_REFUND_STATUSES = ("proposed", "sending", "pending", "succeeded")
DISPUTE_OUTCOMES = ("open", "won", "lost")
# A payout (phase 4) is Stripe moving the business's balance to its bank account, with Stripe's own status.
PAYOUT_STATUSES = ("pending", "in_transit", "paid", "failed", "canceled")


class PaymentConnection(BusinessScopedBase):
    __tablename__ = "payment_connections"
    provider: Mapped[str] = mapped_column(String(12), default="stripe")
    mode: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="onboarding")
    relay_url: Mapped[str] = mapped_column(String(500))
    relay_client_id: Mapped[str | None] = mapped_column(String(64))
    relay_secret_sealed: Mapped[str | None] = mapped_column(Text)
    stripe_account_id: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(2))
    default_currency: Mapped[str | None] = mapped_column(String(3))
    charges_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    payouts_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    details_submitted: Mapped[bool] = mapped_column(default=False, server_default="false")
    requirements_due: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    disabled_reason: Mapped[str | None] = mapped_column(String(100))
    connected_by: Mapped[uuid.UUID | None]
    connected_at: Mapped[datetime | None]
    last_checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(500))
    # Phase 5 settings: add the invoice's pay link to payment reminders; draft a card refund proposal for money
    # taken after an invoice was settled. Both only change what waits for a person, never what is charged.
    reminder_links: Mapped[bool] = mapped_column(default=True, server_default="true")
    draft_refunds: Mapped[bool] = mapped_column(default=True, server_default="true")
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id"),
        CheckConstraint("provider in ('stripe')", name="provider"),
        CheckConstraint("mode in ('test','live')", name="mode"),
        CheckConstraint(f"status in ({','.join(repr(s) for s in CONNECTION_STATUSES)})", name="status"),
    )


class PaymentEvent(BusinessScopedBase):
    __tablename__ = "payment_events"
    stripe_event_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(80))
    account_id: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str | None] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(80))
    livemode: Mapped[bool] = mapped_column(default=False)
    stripe_created_at: Mapped[datetime | None]
    received_at: Mapped[datetime]
    processed_at: Mapped[datetime | None]
    outcome: Mapped[str] = mapped_column(String(10), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id", "stripe_event_id"),
        CheckConstraint(f"outcome in ({','.join(repr(s) for s in EVENT_OUTCOMES)})", name="outcome"),
        Index("ix_payment_events_outcome", "business_id", "outcome"),
    )


class PaymentLink(BusinessScopedBase):
    """The pay link of one invoice. The token is signed (tokens.py) and only its hash is stored; resetting a link
    revokes it and makes a new row, so an old link stops working."""

    __tablename__ = "payment_links"
    invoice_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID | None]
    revoked_at: Mapped[datetime | None]
    last_opened_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        UniqueConstraint("token_hash"),
        Index(
            "uq_payment_links_active",
            "business_id",
            "invoice_id",
            unique=True,
            postgresql_where=text("revoked_at is null"),
        ),
    )


class PaymentRequest(BusinessScopedBase):
    """One Stripe Checkout Session for an invoice's open balance. No card data: Stripe ids and amounts only."""

    __tablename__ = "payment_requests"
    link_id: Mapped[uuid.UUID]
    invoice_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(String(12), default="creating")
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    checkout_session_id: Mapped[str | None] = mapped_column(String(255))
    checkout_url: Mapped[str | None] = mapped_column(String(1000))
    expires_at: Mapped[datetime]
    payment_intent_id: Mapped[str | None] = mapped_column(String(80))
    method: Mapped[str | None] = mapped_column(String(16))
    # What reached the invoice, and what could not because the invoice was already settled (to pay back or keep).
    applied_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    surplus_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    # Stripe's processing fee and what reached the Stripe balance (phase 4), read once the payment is paid.
    stripe_fee_minor: Mapped[int | None] = mapped_column(BigInteger)
    net_minor: Mapped[int | None] = mapped_column(BigInteger)
    invoice_payment_id: Mapped[uuid.UUID | None]
    paid_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    last_checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = scoped_table_args(
        UniqueConstraint("checkout_session_id"),
        ForeignKeyConstraint(["business_id", "link_id"], ["payment_links.business_id", "payment_links.id"]),
        Index("ix_payment_requests_invoice", "business_id", "invoice_id"),
        Index("ix_payment_requests_status", "business_id", "status"),
        CheckConstraint(f"status in ({','.join(repr(s) for s in REQUEST_STATUSES)})", name="status"),
        CheckConstraint("amount_minor > 0", name="amount"),
        CheckConstraint("applied_minor >= 0 and surplus_minor >= 0", name="split"),
    )


class PaymentRefund(BusinessScopedBase):
    """Money paid back to a customer's card from one paid payment request. Sent from Tandem only after approval
    (source `credit_note` or `surplus`), or found in Stripe after someone refunded in the Dashboard (`dashboard`).
    The row id is the Stripe idempotency key, so one approved refund is never sent twice."""

    __tablename__ = "payment_refunds"
    request_id: Mapped[uuid.UUID]
    invoice_id: Mapped[uuid.UUID]
    source: Mapped[str] = mapped_column(String(12))
    invoice_refund_id: Mapped[uuid.UUID | None]  # the sales refund (credit note) this pays out
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(12), default="proposed")
    proposal_id: Mapped[uuid.UUID | None]
    stripe_refund_id: Mapped[str | None] = mapped_column(String(80))
    failure_reason: Mapped[str | None] = mapped_column(String(80))
    requested_by: Mapped[uuid.UUID | None]
    sent_at: Mapped[datetime | None]
    settled_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = scoped_table_args(
        UniqueConstraint("stripe_refund_id"),
        ForeignKeyConstraint(["business_id", "request_id"], ["payment_requests.business_id", "payment_requests.id"]),
        Index("ix_payment_refunds_invoice", "business_id", "invoice_id"),
        Index(
            "uq_payment_refunds_credit",
            "business_id",
            "invoice_refund_id",
            unique=True,
            postgresql_where=text(
                f"invoice_refund_id is not null and status in ({','.join(repr(s) for s in LIVE_REFUND_STATUSES)})"
            ),
        ),
        CheckConstraint(f"source in ({','.join(repr(s) for s in REFUND_SOURCES)})", name="source"),
        CheckConstraint(f"status in ({','.join(repr(s) for s in REFUND_STATUSES)})", name="status"),
        # A credit note's refund always names it, a surplus never does, and a Dashboard refund may once matched.
        CheckConstraint(CREDIT_NOTE_CHECK, name="credit_note"),
        CheckConstraint("amount_minor > 0", name="amount"),
    )


class PaymentDispute(BusinessScopedBase):
    """A customer's bank disputing a card payment (a chargeback). The evidence and the answer stay in Stripe's
    Dashboard; Tandem tracks the deadline and, when the dispute is lost, takes the money back off the invoice."""

    __tablename__ = "payment_disputes"
    request_id: Mapped[uuid.UUID]
    invoice_id: Mapped[uuid.UUID]
    stripe_dispute_id: Mapped[str] = mapped_column(String(80))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    stripe_status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(8), default="open")
    due_by: Mapped[datetime | None]
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    reversed_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = scoped_table_args(
        UniqueConstraint("stripe_dispute_id"),
        ForeignKeyConstraint(["business_id", "request_id"], ["payment_requests.business_id", "payment_requests.id"]),
        Index("ix_payment_disputes_invoice", "business_id", "invoice_id"),
        CheckConstraint(f"outcome in ({','.join(repr(s) for s in DISPUTE_OUTCOMES)})", name="outcome"),
        CheckConstraint("amount_minor > 0 and reversed_minor >= 0", name="amount"),
    )


class PaymentPayout(BusinessScopedBase):
    """Stripe paying the business's Stripe balance out to its bank (phase 4). Totals come from Stripe's own list of
    what the payout contained (charges, refunds, disputes, fees), so the books can move the money out of the Stripe
    clearing account and record Stripe's fees. A manual payout has no such list: it is shown, not summarised."""

    __tablename__ = "payment_payouts"
    stripe_payout_id: Mapped[str] = mapped_column(String(80))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(12))
    arrival_date: Mapped[datetime | None]
    automatic: Mapped[bool]
    method: Mapped[str | None] = mapped_column(String(16))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    charges_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    refunds_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    disputes_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    other_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    fees_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    transaction_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    summarized_at: Mapped[datetime | None]
    summary_complete: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    first_seen_at: Mapped[datetime]
    last_error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = scoped_table_args(
        UniqueConstraint("stripe_payout_id"),
        Index("ix_payment_payouts_arrival", "business_id", "arrival_date"),
        CheckConstraint(f"status in ({','.join(repr(s) for s in PAYOUT_STATUSES)})", name="status"),
    )
