"""The business's link to Stripe through the payments relay, and every Stripe event the relay forwarded.

No card data is ever stored here: Stripe ids, readiness flags and what Stripe still asks for. The relay credential
(client id and shared secret) is stored sealed and never leaves this module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args

CONNECTION_STATUSES = ("onboarding", "active", "restricted", "disconnected")
EVENT_OUTCOMES = ("pending", "applied", "ignored", "failed")


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
