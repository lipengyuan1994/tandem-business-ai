from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ConnectionStatus = Literal["not_connected", "onboarding", "active", "restricted", "disconnected"]


class PaymentConnectionOut(BaseModel):
    configured: bool
    status: ConnectionStatus
    mode: Literal["test", "live"] | None = None
    business_currency: str
    stripe_account_id: str | None = None
    country: str | None = None
    default_currency: str | None = None
    charges_enabled: bool = False
    payouts_enabled: bool = False
    details_submitted: bool = False
    requirements_due: list[str] = Field(default_factory=list)
    disabled_reason: str | None = None
    connected_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_error: str | None = None
    # Stripe's own Dashboard for the business's account (full-Dashboard accounts sign in there directly).
    dashboard_url: str | None = None
    version: int | None = None


class StripeOnboardingIn(BaseModel):
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$", description="Where the business is registered")


class StripeOnboardingOut(BaseModel):
    url: str = Field(description="Stripe's onboarding page; single use and short-lived, open it straight away")


class PaymentDisconnectIn(BaseModel):
    version: int


class RelayEventObject(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    object: str | None = Field(default=None, max_length=40)


class RelayEventIn(BaseModel):
    """What the relay forwards for one Stripe event: never the Stripe object itself."""

    id: str = Field(pattern=r"^evt_[A-Za-z0-9_]{1,76}$")
    type: str = Field(min_length=1, max_length=80)
    account: str = Field(pattern=r"^acct_[A-Za-z0-9]{1,58}$")
    business_ref: uuid.UUID
    created: int | None = None
    livemode: bool = False
    object: RelayEventObject = Field(default_factory=RelayEventObject)


class RelayEventOut(BaseModel):
    status: Literal["accepted", "duplicate"]


RequestStatus = Literal["creating", "open", "processing", "paid", "expired", "failed"]
Unavailable = Literal["not_configured", "regulated_profile", "not_connected", "not_active", "not_issued", "settled"]


class PaymentRequestOut(BaseModel):
    id: uuid.UUID
    status: RequestStatus
    amount_minor: int
    currency: str
    method: str | None = None
    applied_minor: int = 0
    surplus_minor: int = Field(
        default=0, description="Money taken after the invoice was already settled: pay it back from Stripe or keep it"
    )
    created_at: datetime
    paid_at: datetime | None = None
    stripe_fee_minor: int | None = Field(default=None, description="Stripe's processing fee, once Stripe settled it")
    net_minor: int | None = Field(default=None, description="What reached the Stripe balance after Stripe's fee")


RefundSource = Literal["credit_note", "surplus", "dashboard"]
RefundStatus = Literal["proposed", "sending", "pending", "succeeded", "failed", "canceled"]


class CardRefundOut(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    source: RefundSource
    amount_minor: int
    currency: str
    status: RefundStatus
    failure_reason: str | None = Field(default=None, description="Stripe's reason when a refund failed")
    proposal_id: uuid.UUID | None = None
    credit_note_number: str | None = None
    created_at: datetime
    settled_at: datetime | None = None


class RefundOption(BaseModel):
    """Something that may be paid back to the customer's card now, from one paid card payment."""

    request_id: uuid.UUID
    source: Literal["credit_note", "surplus"]
    invoice_refund_id: uuid.UUID | None = None
    credit_note_number: str | None = None
    amount_minor: int
    currency: str


class DisputeOut(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    amount_minor: int
    currency: str
    stripe_status: str
    reason: str | None = None
    outcome: Literal["open", "won", "lost"]
    due_by: datetime | None = Field(default=None, description="Answer in Stripe's Dashboard before this")
    opened_at: datetime
    closed_at: datetime | None = None
    reversed_minor: int = Field(default=0, description="What a lost dispute took back off the invoice")
    last_error: str | None = None


class CardRefundIn(BaseModel):
    """Ask for a card refund. The amount is not chosen here: it is the credit note's refund, or what is left of the
    surplus, so a refund never pays back more than the invoice's own records say."""

    request_id: uuid.UUID
    source: Literal["credit_note", "surplus"]
    invoice_refund_id: uuid.UUID | None = None


class CardRefundProposedOut(BaseModel):
    refund: CardRefundOut
    proposal_id: uuid.UUID


class CardRefundPayload(BaseModel):
    """Approvals payload for `payments.card_refund`."""

    refund_id: uuid.UUID


RefundSource = Literal["credit_note", "surplus", "dashboard"]
RefundStatus = Literal["proposed", "sending", "pending", "succeeded", "failed", "canceled"]


class CardRefundOut(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    source: RefundSource
    amount_minor: int
    currency: str
    status: RefundStatus
    failure_reason: str | None = Field(default=None, description="Stripe's reason when a refund failed")
    proposal_id: uuid.UUID | None = None
    credit_note_number: str | None = None
    created_at: datetime
    settled_at: datetime | None = None


class RefundOption(BaseModel):
    """Something that may be paid back to the customer's card now, from one paid card payment."""

    request_id: uuid.UUID
    source: Literal["credit_note", "surplus"]
    invoice_refund_id: uuid.UUID | None = None
    credit_note_number: str | None = None
    amount_minor: int
    currency: str


class DisputeOut(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    amount_minor: int
    currency: str
    stripe_status: str
    reason: str | None = None
    outcome: Literal["open", "won", "lost"]
    due_by: datetime | None = Field(default=None, description="Answer in Stripe's Dashboard before this")
    opened_at: datetime
    closed_at: datetime | None = None
    reversed_minor: int = Field(default=0, description="What a lost dispute took back off the invoice")
    last_error: str | None = None


class CardRefundIn(BaseModel):
    """Ask for a card refund. The amount is not chosen here: it is the credit note's refund, or what is left of the
    surplus, so a refund never pays back more than the invoice's own records say."""

    request_id: uuid.UUID
    source: Literal["credit_note", "surplus"]
    invoice_refund_id: uuid.UUID | None = None


class CardRefundProposedOut(BaseModel):
    refund: CardRefundOut
    proposal_id: uuid.UUID


class CardRefundPayload(BaseModel):
    """Approvals payload for `payments.card_refund`."""

    refund_id: uuid.UUID


class InvoiceOnlinePaymentsOut(BaseModel):
    invoice_id: uuid.UUID
    available: bool = Field(description="Whether a pay link can be shared for this invoice now")
    unavailable_reason: Unavailable | None = None
    link_url: str | None = None
    link_created_at: datetime | None = None
    link_last_opened_at: datetime | None = None
    mode: Literal["test", "live"] | None = None
    requests: list[PaymentRequestOut] = Field(default_factory=list)
    refunds: list[CardRefundOut] = Field(default_factory=list)
    refund_options: list[RefundOption] = Field(
        default_factory=list, description="Card refunds the reader may ask for now (empty without the permission)"
    )
    disputes: list[DisputeOut] = Field(default_factory=list)


class PayLinkOut(BaseModel):
    url: str


PayPageState = Literal["payable", "processing", "paid", "unavailable"]


class PayPageOut(BaseModel):
    """What a customer with the pay link sees. No customer details beyond what the invoice itself shows."""

    business_name: str
    invoice_number: str | None
    currency: str
    total_minor: int
    balance_minor: int
    issue_date: date | None
    due_date: date | None
    state: PayPageState
    test_mode: bool = False


class CheckoutStartOut(BaseModel):
    url: str = Field(description="Stripe's hosted payment page for the open balance")


PayoutStatus = Literal["pending", "in_transit", "paid", "failed", "canceled"]


class PayoutOut(BaseModel):
    """Stripe paying the business's Stripe balance out to its bank. Totals are Stripe's own, from what the payout
    contained; a manual payout (started in the Stripe Dashboard) has none."""

    id: uuid.UUID
    amount_minor: int
    currency: str
    status: PayoutStatus
    arrival_date: date | None = None
    automatic: bool
    method: str | None = None
    failure_code: str | None = None
    charges_minor: int = 0
    refunds_minor: int = 0
    disputes_minor: int = 0
    other_minor: int = 0
    fees_minor: int = 0
    transaction_count: int = 0
    summarized: bool = False
    summary_complete: bool = False
