from __future__ import annotations

import uuid
from datetime import datetime
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
