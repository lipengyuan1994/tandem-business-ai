"""CRM rules (M03): no auto-merge; consent is never inferred and is append-only;
contact values are unique among active customers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.platform import audit, clock, crud, events
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid, NotFound
from app.platform.events import DomainEventPayload
from app.platform.ids import new_id

from . import permissions as P
from .models import ConsentRecord, ContactPoint, Customer, CustomerNote, CustomerTag, Tag, TimelineEntry
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
    NoteIn,
    TimelineEntryIn,
    normalize,
)

FIELDS = [
    "display_name",
    "given_name",
    "family_name",
    "organization_name",
    "preferred_language",
    "timezone",
    "preferred_contact_channel",
]
REDACT = ["display_name", "given_name", "family_name"]


class CustomerCreated(DomainEventPayload):
    event_type = "crm.customer_created"
    customer_id: uuid.UUID
    source: str


class ConsentChanged(DomainEventPayload):
    event_type = "crm.consent_changed"
    customer_id: uuid.UUID
    channel: str
    purpose: str
    status: str


def _customer(db: Session, ctx: Context, customer_id: uuid.UUID, *, for_update: bool = False) -> Customer:
    c = crud.require_scoped(db, ctx, Customer, customer_id, "crm.customer_not_found", for_update=for_update)
    if c.status in ("merged", "erased", "restricted"):
        raise NotFound("crm.customer_not_found")
    return c


def _add_contact(db: Session, ctx: Context, customer_id: uuid.UUID, cp: ContactPointIn) -> ContactPoint:
    normalized = normalize(cp.type, cp.value)
    clash = db.scalar(
        select(ContactPoint.id).where(
            ContactPoint.business_id == ctx.business_id,
            ContactPoint.type == cp.type,
            ContactPoint.value_normalized == normalized,
            ContactPoint.is_active.is_(True),
        )
    )
    if clash:
        raise Conflict("crm.contact_point_in_use", detail=cp.type)
    if cp.is_primary:
        db.execute(
            update(ContactPoint)
            .where(
                ContactPoint.business_id == ctx.business_id,
                ContactPoint.customer_id == customer_id,
                ContactPoint.type == cp.type,
            )
            .values(is_primary=False)
        )
    has_primary = db.scalar(
        select(ContactPoint.id).where(
            ContactPoint.business_id == ctx.business_id,
            ContactPoint.customer_id == customer_id,
            ContactPoint.type == cp.type,
            ContactPoint.is_primary.is_(True),
            ContactPoint.is_active.is_(True),
        )
    )
    assert ctx.business_id is not None
    row = ContactPoint(
        id=new_id(),
        business_id=ctx.business_id,
        customer_id=customer_id,
        type=cp.type,
        value=cp.value.strip(),
        value_normalized=normalized,
        is_primary=cp.is_primary or not has_primary,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _set_tags(db: Session, ctx: Context, customer_id: uuid.UUID, names: list[str]) -> None:
    assert ctx.business_id is not None
    db.execute(
        delete(CustomerTag).where(CustomerTag.business_id == ctx.business_id, CustomerTag.customer_id == customer_id)
    )
    for name in sorted({n.strip().lower() for n in names if n.strip()}):
        tag_id = db.scalar(
            pg_insert(Tag)
            .values(id=new_id(), business_id=ctx.business_id, name=name)
            .on_conflict_do_update(index_elements=["business_id", "name"], set_={"name": name})
            .returning(Tag.id)
        )
        db.add(CustomerTag(business_id=ctx.business_id, customer_id=customer_id, tag_id=tag_id))
    db.flush()


def record_consent(
    db: Session, ctx: Context, customer_id: uuid.UUID, c: ConsentIn, *, recorded_at: datetime | None = None
) -> ConsentRecord:
    """Append-only: a change is a new record; the latest one per (channel, purpose) is current."""
    from .booking import lock_customers

    lock_customers(db, ctx, {customer_id})
    assert ctx.business_id is not None
    row = ConsentRecord(
        id=new_id(),
        business_id=ctx.business_id,
        customer_id=customer_id,
        channel=c.channel,
        purpose=c.purpose,
        status=c.status,
        source=c.source,
        evidence=c.evidence,
        recorded_by=ctx.user_id,
        recorded_at=recorded_at if recorded_at is not None else clock.now(),
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        ctx,
        action="crm.consent.recorded",
        target_type="customer",
        target_id=customer_id,
        summary=f"Consent {c.channel}/{c.purpose}: {c.status} ({c.source})",
    )
    events.publish(
        db, ctx, ConsentChanged(customer_id=customer_id, channel=c.channel, purpose=c.purpose, status=c.status)
    )
    return row


def create_customer(db: Session, ctx: Context, data: CustomerCreate) -> Customer:
    ctx.require(P.CUSTOMER_WRITE)
    if data.consents:
        ctx.require(P.CONSENT_WRITE)
    assert ctx.business_id is not None
    source = {"assistant": "assistant", "visitor": "public_chat"}.get(ctx.actor_kind, "staff")
    customer = Customer(
        id=new_id(),
        business_id=ctx.business_id,
        status="active",
        source=source,
        created_by=ctx.user_id or ctx.on_behalf_of_user_id,
        preferred_contact_channel=data.preferred_contact_channel or "email",
        **data.model_dump(exclude={"contact_points", "consents", "tags", "preferred_contact_channel"}),
    )
    db.add(customer)
    db.flush()
    for cp in data.contact_points:
        _add_contact(db, ctx, customer.id, cp)
    for consent in data.consents:
        record_consent(db, ctx, customer.id, consent)
    if data.tags:
        _set_tags(db, ctx, customer.id, data.tags)
    audit.record(
        db,
        ctx,
        action="crm.customer.created",
        target_type="customer",
        target_id=customer.id,
        summary="Customer created",
    )
    events.publish(db, ctx, CustomerCreated(customer_id=customer.id, source=source))
    return customer


def update_customer(
    db: Session, ctx: Context, customer_id: uuid.UUID, version: int, changes: CustomerFields
) -> Customer:
    ctx.require(P.CUSTOMER_WRITE)
    from .booking import lock_customers

    lock_customers(db, ctx, {customer_id})
    before = _customer(db, ctx, customer_id)
    snapshot = {f: getattr(before, f) for f in FIELDS}
    values = changes.model_dump(exclude_unset=True)
    after = crud.update_versioned(db, ctx, Customer, customer_id, version, values, "crm.customer_version_conflict")
    audit.record(
        db,
        ctx,
        action="crm.customer.updated",
        target_type="customer",
        target_id=customer_id,
        summary=f"Customer updated ({', '.join(sorted(values))})",
        changes=audit.diff(snapshot, after, FIELDS, redact=REDACT),
    )
    return after


def archive_customer(db: Session, ctx: Context, customer_id: uuid.UUID, version: int) -> Customer:
    ctx.require(P.CUSTOMER_WRITE)
    _customer(db, ctx, customer_id)
    after = crud.update_versioned(
        db, ctx, Customer, customer_id, version, {"status": "archived"}, "crm.customer_version_conflict"
    )
    db.execute(
        update(ContactPoint)
        .where(ContactPoint.business_id == ctx.business_id, ContactPoint.customer_id == customer_id)
        .values(is_active=False)
    )
    audit.record(
        db,
        ctx,
        action="crm.customer.archived",
        target_type="customer",
        target_id=customer_id,
        summary="Customer archived",
    )
    return after


def add_contact_point(db: Session, ctx: Context, customer_id: uuid.UUID, cp: ContactPointIn) -> ContactPoint:
    ctx.require(P.CUSTOMER_WRITE)
    _customer(db, ctx, customer_id, for_update=True)
    row = _add_contact(db, ctx, customer_id, cp)
    audit.record(
        db,
        ctx,
        action="crm.contact_point.added",
        target_type="customer",
        target_id=customer_id,
        summary=f"{cp.type} added",
    )
    return row


def remove_contact_point(db: Session, ctx: Context, contact_point_id: uuid.UUID) -> None:
    ctx.require(P.CUSTOMER_WRITE)
    cp = crud.require_scoped(db, ctx, ContactPoint, contact_point_id, "crm.contact_point_not_found")
    from .booking import lock_customers

    lock_customers(db, ctx, {cp.customer_id})
    cp.is_active, cp.is_primary = False, False
    audit.record(
        db,
        ctx,
        action="crm.contact_point.removed",
        target_type="customer",
        target_id=cp.customer_id,
        summary=f"{cp.type} removed",
    )


def set_tags(db: Session, ctx: Context, customer_id: uuid.UUID, names: list[str]) -> list[str]:
    ctx.require(P.CUSTOMER_WRITE)
    _customer(db, ctx, customer_id)
    _set_tags(db, ctx, customer_id, names)
    return tags_of(db, ctx, [customer_id]).get(customer_id, [])


def add_note(db: Session, ctx: Context, customer_id: uuid.UUID, note: NoteIn) -> CustomerNote:
    ctx.require(P.CUSTOMER_WRITE)
    _customer(db, ctx, customer_id)
    assert ctx.business_id is not None
    row = CustomerNote(
        id=new_id(),
        business_id=ctx.business_id,
        customer_id=customer_id,
        body=note.body,
        pinned=note.pinned,
        author_id=ctx.user_id,
    )
    db.add(row)
    db.flush()
    add_timeline_entry(
        db,
        ctx,
        TimelineEntryIn(
            customer_id=customer_id,
            occurred_at=clock.now(),
            kind="note",
            ref_module="crm",
            ref_type="note",
            ref_id=row.id,
            summary="Note added",
        ),
    )
    return row


# ------------------------------------------------------------------------------------------------------ reads
def tags_of(db: Session, ctx: Context, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    rows = db.execute(
        select(CustomerTag.customer_id, Tag.name)
        .join(Tag, Tag.id == CustomerTag.tag_id)
        .where(CustomerTag.business_id == ctx.business_id, CustomerTag.customer_id.in_(customer_ids))
        .order_by(Tag.name)
    )
    out: dict[uuid.UUID, list[str]] = {}
    for cid, name in rows:
        out.setdefault(cid, []).append(name)
    return out


def contacts_of(db: Session, ctx: Context, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ContactPoint]]:
    rows = db.scalars(
        select(ContactPoint)
        .where(
            ContactPoint.business_id == ctx.business_id,
            ContactPoint.customer_id.in_(customer_ids),
            ContactPoint.is_active.is_(True),
        )
        .order_by(ContactPoint.is_primary.desc(), ContactPoint.created_at)
    )
    out: dict[uuid.UUID, list[ContactPoint]] = {}
    for r in rows:
        out.setdefault(r.customer_id, []).append(r)
    return out


def current_consents(db: Session, ctx: Context, customer_id: uuid.UUID) -> list[ConsentRecord]:
    stmt = (
        select(ConsentRecord)
        .where(ConsentRecord.business_id == ctx.business_id, ConsentRecord.customer_id == customer_id)
        .order_by(
            ConsentRecord.channel, ConsentRecord.purpose, ConsentRecord.recorded_at.desc(), ConsentRecord.id.desc()
        )
        .ext(distinct_on(ConsentRecord.channel, ConsentRecord.purpose))
    )
    return list(db.scalars(stmt))


def consent_history(db: Session, ctx: Context, customer_id: uuid.UUID) -> list[ConsentRecord]:
    ctx.require(P.CONSENT_READ)
    _customer(db, ctx, customer_id)
    return list(
        db.scalars(
            select(ConsentRecord)
            .where(ConsentRecord.business_id == ctx.business_id, ConsentRecord.customer_id == customer_id)
            .order_by(ConsentRecord.recorded_at.desc(), ConsentRecord.id.desc())
        )
    )


def list_customers(
    db: Session, ctx: Context, *, q: str | None, tag: str | None, status: str, limit: int, cursor: str | None
) -> tuple[list[Customer], str | None]:
    ctx.require(P.CUSTOMER_READ)
    stmt = select(Customer).where(
        Customer.business_id == ctx.business_id,
        Customer.status == status,
        Customer.status != "restricted",
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            Customer.display_name.ilike(like)
            | Customer.id.in_(
                select(ContactPoint.customer_id).where(
                    ContactPoint.business_id == ctx.business_id,
                    ContactPoint.value_normalized.ilike(like.lower()),
                    ContactPoint.is_active.is_(True),
                )
            )
        )
    if tag:
        stmt = stmt.where(
            Customer.id.in_(
                select(CustomerTag.customer_id)
                .join(Tag, Tag.id == CustomerTag.tag_id)
                .where(Tag.business_id == ctx.business_id, Tag.name == tag.lower())
            )
        )
    return crud.page(db, stmt, Customer, limit, cursor)


def get_customer(db: Session, ctx: Context, customer_id: uuid.UUID) -> Customer:
    ctx.require(P.CUSTOMER_READ)
    return _customer(db, ctx, customer_id)


def summary(db: Session, ctx: Context, customer_id: uuid.UUID) -> CustomerSummary:
    c = _customer(db, ctx, customer_id)
    types = sorted({cp.type for cp in contacts_of(db, ctx, [c.id]).get(c.id, [])})
    return CustomerSummary(
        id=c.id,
        display_name=c.display_name,
        kind=c.kind,
        status=c.status,
        contact_types=types,
        tags=tags_of(db, ctx, [c.id]).get(c.id, []),
        timezone=c.timezone,
        last_interaction_at=c.last_interaction_at,
    )


def find_candidates(
    db: Session,
    ctx: Context,
    *,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    limit: int = 5,
) -> list[CustomerCandidate]:
    """Deterministic matching; never auto-merges.

    Exact email/phone match scores 1.0; trigram name similarity >= 0.3 scores similarity x 0.8.
    """
    ctx.require(P.CUSTOMER_READ)
    found: dict[uuid.UUID, CustomerCandidate] = {}
    for kind, value in (("email", email), ("phone", phone)):
        if not value:
            continue
        try:
            normalized = normalize(kind, value)
        except Invalid:  # an invalid email/phone simply matches nothing
            continue
        rows = db.execute(
            select(Customer.id, Customer.display_name)
            .join(ContactPoint, ContactPoint.customer_id == Customer.id)
            .where(
                Customer.business_id == ctx.business_id,
                Customer.status == "active",
                ContactPoint.type == kind,
                ContactPoint.value_normalized == normalized,
                ContactPoint.is_active.is_(True),
            )
        )
        for cid, dname in rows:
            found[cid] = CustomerCandidate(customer_id=cid, display_name=dname, score=1.0, reason=kind)
    if name and name.strip():
        name_rows = db.execute(
            text(
                """select id, display_name, similarity(lower(display_name), lower(:n)) as s from customers
               where business_id = :b and status = 'active' and similarity(lower(display_name), lower(:n)) >= 0.3
               order by s desc limit :l"""
            ),
            {"n": name.strip(), "b": ctx.business_id, "l": limit},
        )
        for row in name_rows:
            cid, dname, sim = row[0], row[1], row[2]
            if cid not in found:
                found[cid] = CustomerCandidate(
                    customer_id=cid, display_name=dname, score=round(float(sim) * 0.8, 3), reason="name"
                )
    return sorted(found.values(), key=lambda c: -c.score)[:limit]


def add_timeline_entry(db: Session, ctx: Context, entry: TimelineEntryIn) -> None:
    """Idempotent per (ref_type, ref_id, kind). Updates last_interaction_at."""
    assert ctx.business_id is not None
    db.execute(
        pg_insert(TimelineEntry)
        .values(id=new_id(), business_id=ctx.business_id, **entry.model_dump())
        .on_conflict_do_nothing(index_elements=["business_id", "ref_type", "ref_id", "kind"])
    )
    db.execute(
        update(Customer)
        .where(Customer.business_id == ctx.business_id, Customer.id == entry.customer_id)
        .where((Customer.last_interaction_at.is_(None)) | (Customer.last_interaction_at < entry.occurred_at))
        .values(last_interaction_at=entry.occurred_at)
    )


def timeline(db: Session, ctx: Context, customer_id: uuid.UUID, limit: int = 50) -> list[TimelineEntry]:
    ctx.require(P.CUSTOMER_READ)
    _customer(db, ctx, customer_id)
    return list(
        db.scalars(
            select(TimelineEntry)
            .where(TimelineEntry.business_id == ctx.business_id, TimelineEntry.customer_id == customer_id)
            .order_by(TimelineEntry.occurred_at.desc())
            .limit(min(limit, 200))
        )
    )


def notes(db: Session, ctx: Context, customer_id: uuid.UUID) -> list[CustomerNote]:
    ctx.require(P.CUSTOMER_READ)
    _customer(db, ctx, customer_id)
    return list(
        db.scalars(
            select(CustomerNote)
            .where(CustomerNote.business_id == ctx.business_id, CustomerNote.customer_id == customer_id)
            .order_by(CustomerNote.pinned.desc(), CustomerNote.created_at.desc())
        )
    )


def consent_status(db: Session, ctx: Context, customer_id: uuid.UUID, channel: str, purpose: str) -> ConsentStatus:
    current = {(r.channel, r.purpose): r.status for r in current_consents(db, ctx, customer_id)}
    return ConsentStatus(channel=channel, purpose=purpose, status=current.get((channel, purpose), "none"))


def marketing_eligible(
    db: Session, ctx: Context, channel: str, customer_ids: list[uuid.UUID] | None = None
) -> list[EligibleContact]:
    """Active customers with a primary contact for `channel` whose *latest* marketing consent for it is 'granted'."""
    contact_type = "email" if channel == "email" else "phone"
    rows = db.execute(
        text(
            """with latest as (
             select distinct on (customer_id) customer_id, status from consent_records
              where business_id = :b and channel = :ch and purpose = 'marketing'
              order by customer_id, recorded_at desc, id desc)
           select c.id, c.display_name, cp.id as cp_id, cp.value from customers c
             join latest l on l.customer_id = c.id and l.status = 'granted'
             join contact_points cp on cp.customer_id = c.id and cp.type = :ct and cp.is_primary and cp.is_active
            where c.business_id = :b and c.status = 'active'
              and (cast(:ids as uuid[]) is null or c.id = any(cast(:ids as uuid[])))
            order by c.display_name"""
        ),
        {"b": ctx.business_id, "ch": channel, "ct": contact_type, "ids": customer_ids},
    )
    return [EligibleContact(customer_id=r[0], display_name=r[1], contact_point_id=r[2], value=r[3]) for r in rows]


SEGMENT_FIELDS = {
    "tag": {"has"},
    "last_interaction_at": {"older_than_days", "newer_than_days"},
    "created_at": {"older_than_days", "newer_than_days"},
    "customer_kind": {"eq"},
}


def customer_ids_where(db: Session, ctx: Context, field: str, op: str, value: Any) -> set[uuid.UUID]:
    """Customers (any status) matching one whitelisted clause. Unknown fields or operators are rejected."""
    if op not in SEGMENT_FIELDS.get(field, set()):
        raise Invalid("crm.invalid_filter", detail=f"{field} {op}")
    b = Customer.business_id == ctx.business_id
    if field == "tag":
        stmt = (
            select(CustomerTag.customer_id)
            .join(Tag, (Tag.business_id == CustomerTag.business_id) & (Tag.id == CustomerTag.tag_id))
            .where(CustomerTag.business_id == ctx.business_id, func.lower(Tag.name) == str(value).strip().lower())
        )
    elif field == "customer_kind":
        stmt = select(Customer.id).where(b, Customer.kind == str(value))
    else:
        days = int(value)
        cutoff = clock.now() - timedelta(days=days)
        column = (
            func.coalesce(Customer.last_interaction_at, Customer.created_at)
            if field == "last_interaction_at"
            else Customer.created_at
        )
        stmt = select(Customer.id).where(b, column < cutoff if op == "older_than_days" else column >= cutoff)
    return set(db.scalars(stmt))


def audience_candidates(
    db: Session, ctx: Context, channel: str, customer_ids: list[uuid.UUID]
) -> list[AudienceCandidate]:
    """Per customer: active?, primary contact for the channel, and is its latest marketing consent granted?"""
    if not customer_ids:
        return []
    contact_type = "email" if channel == "email" else "phone"
    rows = db.execute(
        text(
            """with latest as (
             select distinct on (customer_id) customer_id, status from consent_records
              where business_id = :b and channel = :ch and purpose = 'marketing'
                and customer_id = any(cast(:ids as uuid[]))
              order by customer_id, recorded_at desc, id desc)
           select c.id, c.display_name, c.status = 'active', cp.id, cp.value, coalesce(l.status = 'granted', false)
             from customers c
             left join latest l on l.customer_id = c.id
             left join contact_points cp on cp.business_id = c.business_id and cp.customer_id = c.id
                  and cp.type = :ct and cp.is_primary and cp.is_active
            where c.business_id = :b and c.id = any(cast(:ids as uuid[]))
            order by c.display_name, c.id"""
        ),
        {"b": ctx.business_id, "ch": channel, "ct": contact_type, "ids": customer_ids},
    )
    return [
        AudienceCandidate(
            customer_id=r[0], display_name=r[1], is_active=r[2], contact_point_id=r[3], value=r[4], consent_granted=r[5]
        )
        for r in rows
    ]


def primary_contact(db: Session, ctx: Context, customer_id: uuid.UUID, contact_type: str) -> ContactPoint | None:
    return db.scalar(
        select(ContactPoint)
        .where(
            ContactPoint.business_id == ctx.business_id,
            ContactPoint.customer_id == customer_id,
            ContactPoint.type == contact_type,
            ContactPoint.is_active.is_(True),
        )
        .order_by(ContactPoint.is_primary.desc())
        .limit(1)
    )


def counts(db: Session, ctx: Context) -> dict[str, Any]:
    total = db.scalar(
        select(func.count())
        .select_from(Customer)
        .where(Customer.business_id == ctx.business_id, Customer.status == "active")
    )
    return {"active_customers": int(total or 0)}
