"""Promote request evidence only for a matching customer contact, once per evidence item."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.context import Context

from . import service
from .booking import lock_customers
from .models import ConsentRecord, ContactPoint
from .schemas import ConsentIn, normalize


def record_request_contact_consent(
    db: Session,
    ctx: Context,
    customer_id: uuid.UUID,
    *,
    email: str,
    recorded_at: datetime,
    evidence: str,
) -> None:
    # Conversation callers already hold this lock (customer before conversation). Keep this
    # entry point safe for other callers too: a concurrent relink must see the first insert.
    lock_customers(db, ctx, {customer_id})
    service.summary(db, ctx, customer_id)
    contact = db.scalar(
        select(ContactPoint.id).where(
            ContactPoint.business_id == ctx.business_id,
            ContactPoint.customer_id == customer_id,
            ContactPoint.type == "email",
            ContactPoint.is_active.is_(True),
            ContactPoint.value_normalized == normalize("email", email),
        )
    )
    if contact is None:
        return
    existing = db.scalar(
        select(ConsentRecord.id).where(
            ConsentRecord.business_id == ctx.business_id,
            ConsentRecord.customer_id == customer_id,
            ConsentRecord.channel == "email",
            ConsentRecord.purpose == "service_updates",
            ConsentRecord.source == "form",
            ConsentRecord.status == "granted",
            ConsentRecord.evidence == evidence,
            ConsentRecord.recorded_at == recorded_at,
        )
    )
    if existing is None:
        service.record_consent(
            db,
            ctx,
            customer_id,
            ConsentIn(channel="email", purpose="service_updates", status="granted", source="form", evidence=evidence),
            recorded_at=recorded_at,
        )
