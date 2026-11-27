"""Test helpers other modules' tests may use (synthetic data only)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.platform import clock

from .models import PaymentPayout


def add_payout(db: Session, business_id: uuid.UUID, arrival_date: datetime, **fields: Any) -> PaymentPayout:
    """A Stripe payout as the payments module would have stored it after Stripe's events."""
    values: dict[str, Any] = {
        "stripe_payout_id": f"po_{uuid.uuid4().hex[:24]}",
        "amount_minor": 8787,
        "currency": "USD",
        "status": "paid",
        "automatic": True,
        "charges_minor": 9000,
        "fees_minor": 213,
        "transaction_count": 1,
        "summarized_at": clock.now(),
        "summary_complete": True,
        "first_seen_at": clock.now(),
        **fields,
    }
    row = PaymentPayout(business_id=business_id, arrival_date=arrival_date, **values)
    db.add(row)
    db.flush()
    return row


def set_payout_status(db: Session, payout_id: uuid.UUID, status: str) -> None:
    row = db.get(PaymentPayout, payout_id)
    assert row is not None
    row.status, row.version = status, row.version + 1
