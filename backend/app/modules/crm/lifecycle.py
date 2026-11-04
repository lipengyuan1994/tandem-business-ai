"""CRM: export, and erasure of one customer's personal data (M18, 06-security-and-privacy.md#retention-and-erasure).

Erasure keeps the row, its id, its source and the consent *decisions* (who agreed to what, when), because other records
refer to them; it removes everything that identifies the person.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.platform import audit, clock
from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.errors import NotFound
from app.platform.export import dump_models
from app.platform.registry import ErasureTarget

from .models import ConsentRecord, ContactPoint, Customer, CustomerNote, CustomerTag, Tag, TimelineEntry

TOMBSTONE = "Erased customer"


def export(db: Session, ctx: Context) -> dict[str, list[dict[str, Any]]]:
    return dump_models(db, ctx, [Customer, ContactPoint, ConsentRecord, CustomerNote, Tag, CustomerTag, TimelineEntry])


def erasure_target(db: Session, ctx: Context, customer_id: uuid.UUID) -> ErasureTarget:
    """Locks the customer row first, so a second erasure of the same person waits and then sees it is done."""
    customer = db.scalar(
        select(Customer).where(Customer.business_id == ctx.business_id, Customer.id == customer_id).with_for_update()
    )
    if customer is None or customer.status == "merged":
        raise NotFound("crm.customer_not_found")
    if customer.status in ("erased", "restricted"):
        return ErasureTarget(customer_id=customer_id, display_name="", emails=(), phones=())
    # Inactive contact points count too: archiving a customer deactivates them, but copies may still exist elsewhere.
    contacts = [
        c
        for c in db.scalars(
            select(ContactPoint).where(
                ContactPoint.business_id == ctx.business_id, ContactPoint.customer_id == customer_id
            )
        )
        if c.value != "[erased]"
    ]
    names = (customer.given_name, customer.family_name, customer.organization_name)
    return ErasureTarget(
        customer_id=customer_id,
        display_name=customer.display_name,
        emails=tuple(c.value for c in contacts if c.type == "email"),
        phones=tuple(c.value for c in contacts if c.type == "phone"),
        names=tuple(n for n in names if n),
        phones_normalized=tuple(c.value_normalized for c in contacts if c.type == "phone"),
    )


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Idempotent: running it again changes nothing."""
    cid, biz = target.customer_id, ctx.business_id
    customer = db.scalar(select(Customer).where(Customer.business_id == biz, Customer.id == cid).with_for_update())
    if customer is None:
        raise NotFound("crm.customer_not_found")
    already = customer.status == "erased"
    customer.display_name, customer.given_name, customer.family_name = TOMBSTONE, None, None
    customer.organization_name, customer.preferred_language, customer.timezone = None, None, None
    customer.status, customer.version = "erased", customer.version + (0 if already else 1)
    # Addresses are blanked rather than deleted: campaign audiences and old appointments still point at them.
    contacts = db.execute(
        update(ContactPoint)
        .where(ContactPoint.business_id == biz, ContactPoint.customer_id == cid, ContactPoint.value != "[erased]")
        .values(
            value="[erased]", value_normalized=f"erased:{cid}:{uuid.uuid4().hex[:8]}", is_active=False, is_primary=False
        )
    )
    notes = db.execute(delete(CustomerNote).where(CustomerNote.business_id == biz, CustomerNote.customer_id == cid))
    tag_ids = list(
        db.scalars(select(CustomerTag.tag_id).where(CustomerTag.business_id == biz, CustomerTag.customer_id == cid))
    )
    tags = db.execute(delete(CustomerTag).where(CustomerTag.business_id == biz, CustomerTag.customer_id == cid))
    # A tag whose text names the person is deleted once no other customer uses it.
    needles = [n.lower() for n in target.needles]
    personal_tags = [
        tag.id
        for tag in db.scalars(select(Tag).where(Tag.business_id == biz, Tag.id.in_(tag_ids)))
        if any(n in tag.name.lower() for n in needles)
    ]
    still_used = set(
        db.scalars(
            select(CustomerTag.tag_id).where(CustomerTag.business_id == biz, CustomerTag.tag_id.in_(personal_tags))
        )
    )
    tag_rows = db.execute(
        delete(Tag).where(Tag.business_id == biz, Tag.id.in_([t for t in personal_tags if t not in still_used]))
    )
    timeline = db.execute(
        delete(TimelineEntry).where(TimelineEntry.business_id == biz, TimelineEntry.customer_id == cid)
    )
    # Consent decisions are append-only for the application role; a narrow database function clears their free text.
    consent_cleared = int(db.scalar(select(func.erase_redact_consent(biz, cid))) or 0)
    if not already:
        audit.record(
            db,
            ctx,
            action="crm.customer.erased",
            target_type="customer",
            target_id=cid,
            summary="Personal data erased",
            changes={"at": clock.now().isoformat()},
        )
    return {
        "customer_anonymised": 0 if already else 1,
        "contact_points_blanked": rows_affected(contacts),
        "notes_deleted": rows_affected(notes),
        "tags_removed": rows_affected(tags),
        "personal_tags_deleted": rows_affected(tag_rows),
        "timeline_entries_deleted": rows_affected(timeline),
        "consent_evidence_cleared": consent_cleared,
    }


def restrict(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Keep master identity and record text, blank and deactivate delivery contact points."""
    customer = db.scalar(
        select(Customer)
        .where(Customer.business_id == ctx.business_id, Customer.id == target.customer_id)
        .with_for_update()
    )
    if customer is None:
        raise NotFound("crm.customer_not_found")
    customer.status, customer.version = "restricted", customer.version + 1
    contacts = db.execute(
        update(ContactPoint)
        .where(ContactPoint.business_id == ctx.business_id, ContactPoint.customer_id == target.customer_id)
        .values(
            value="[erased]", value_normalized=f"restricted:{target.customer_id}", is_active=False, is_primary=False
        )
    )
    db.flush()
    return {"customers_restricted": 1, "contact_points_blanked": rows_affected(contacts)}
