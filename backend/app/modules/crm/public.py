"""Public API of the crm module. Other modules may import ONLY from this file."""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.registry import ErasureTarget

from . import booking, lifecycle, service
from .booking import lock_customers
from .request_consent import record_request_contact_consent
from .schemas import (
    AudienceCandidate,
    ConsentIn,
    ConsentStatus,
    ContactPointIn,
    CustomerCandidate,
    CustomerCreate,
    CustomerFields,
    CustomerSummary,
    EligibleContact,
    TimelineEntryIn,
    TimelineEntryOut,
    normalize,
)
from .service import ConsentChanged, CustomerCreated


def normalize_contact(kind: str, value: str) -> str:
    """The canonical form of an e-mail address or phone number (also used for suppression lists)."""
    return normalize(kind, value)


def has_identity_name_overlap(name: str | None, candidate_name: str) -> bool:
    """At least two distinct shared name tokens; a surname or any other single token is insufficient.

    A one-letter visitor token counts when it is the initial of another candidate token ("D Example" overlaps
    "Dan Example"; "Bea Example" does not overlap "Ben Example"). This identity check is separate from trigram search
    ranking. Case, whitespace and punctuation are ignored.
    """
    visitor_tokens = set(re.findall(r"[^\W_]+", (name or "").casefold()))
    candidate_tokens = set(re.findall(r"[^\W_]+", candidate_name.casefold()))
    shared = visitor_tokens & candidate_tokens
    remaining = candidate_tokens - shared
    initials = {t for t in visitor_tokens - shared if len(t) == 1 and any(c.startswith(t) for c in remaining)}
    return len(shared) + len(initials) >= 2


def get_erasure_target(db: Session, ctx: Context, customer_id: uuid.UUID) -> ErasureTarget:
    """Name, addresses and numbers of one customer, for the erasure of their data in every module (M18)."""
    return lifecycle.erasure_target(db, ctx, customer_id)


def customer_ids_where(db: Session, ctx: Context, field: str, op: str, value: object) -> set[uuid.UUID]:
    return service.customer_ids_where(db, ctx, field, op, value)


def audience_candidates(
    db: Session, ctx: Context, channel: str, customer_ids: list[uuid.UUID]
) -> list[AudienceCandidate]:
    return service.audience_candidates(db, ctx, channel, customer_ids)


def get_customer_summary(db: Session, ctx: Context, customer_id: uuid.UUID) -> CustomerSummary:
    return service.summary(db, ctx, customer_id)


def get_booking_customer(db: Session, ctx: Context, customer_id: uuid.UUID) -> CustomerSummary:
    """Recheck booking eligibility under the ordered customer locks shared with conversation writes."""
    return booking.customer_for_booking(db, ctx, customer_id)


def is_restricted(db: Session, ctx: Context, customer_id: uuid.UUID) -> bool:
    """Retention holds survive profile switches. Read only the status, even when normal CRM reads are hidden."""
    from sqlalchemy import select

    from .models import Customer

    return (
        db.scalar(select(Customer.status).where(Customer.business_id == ctx.business_id, Customer.id == customer_id))
        == "restricted"
    )


def get_customer_contact(db: Session, ctx: Context, customer_id: uuid.UUID, contact_type: str) -> str | None:
    """Raw contact value for *delivering* a message (never for display to the AI)."""
    cp = service.primary_contact(db, ctx, customer_id, contact_type)
    return cp.value if cp else None


def find_customer_candidates(
    db: Session,
    ctx: Context,
    *,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    limit: int = 5,
) -> list[CustomerCandidate]:
    return service.find_candidates(db, ctx, name=name, email=email, phone=phone, limit=limit)


def get_timeline(db: Session, ctx: Context, customer_id: uuid.UUID, limit: int = 50) -> list[TimelineEntryOut]:
    return [TimelineEntryOut.model_validate(t) for t in service.timeline(db, ctx, customer_id, limit)]


def add_timeline_entry(db: Session, ctx: Context, entry: TimelineEntryIn) -> None:
    service.add_timeline_entry(db, ctx, entry)


def current_consent(db: Session, ctx: Context, customer_id: uuid.UUID, channel: str, purpose: str) -> ConsentStatus:
    return service.consent_status(db, ctx, customer_id, channel, purpose)


def list_marketing_eligible(
    db: Session, ctx: Context, channel: str, customer_ids: list[uuid.UUID] | None = None
) -> list[EligibleContact]:
    return service.marketing_eligible(db, ctx, channel, customer_ids)


def record_consent(db: Session, ctx: Context, customer_id: uuid.UUID, consent: ConsentIn) -> None:
    service.record_consent(db, ctx, customer_id, consent)


def create_customer(db: Session, ctx: Context, data: CustomerCreate) -> CustomerSummary:
    return service.summary(db, ctx, service.create_customer(db, ctx, data).id)


def update_customer(
    db: Session, ctx: Context, customer_id: uuid.UUID, version: int, changes: CustomerFields
) -> CustomerSummary:
    service.update_customer(db, ctx, customer_id, version, changes)
    return service.summary(db, ctx, customer_id)


def customer_names(db: Session, ctx: Context, customer_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    from sqlalchemy import select

    from .models import Customer

    if not customer_ids:
        return {}
    rows = db.execute(
        select(Customer.id, Customer.display_name).where(
            Customer.business_id == ctx.business_id, Customer.id.in_(customer_ids)
        )
    )
    return {r[0]: r[1] for r in rows}


__all__ = [
    "AudienceCandidate",
    "ConsentChanged",
    "ConsentIn",
    "ConsentStatus",
    "ContactPointIn",
    "CustomerCandidate",
    "CustomerCreate",
    "CustomerCreated",
    "CustomerFields",
    "CustomerSummary",
    "EligibleContact",
    "TimelineEntryIn",
    "TimelineEntryOut",
    "add_timeline_entry",
    "audience_candidates",
    "create_customer",
    "current_consent",
    "customer_ids_where",
    "customer_names",
    "find_customer_candidates",
    "get_booking_customer",
    "get_customer_contact",
    "get_customer_summary",
    "get_erasure_target",
    "get_timeline",
    "has_identity_name_overlap",
    "is_restricted",
    "list_marketing_eligible",
    "lock_customers",
    "normalize_contact",
    "record_consent",
    "record_request_contact_consent",
    "update_customer",
]
