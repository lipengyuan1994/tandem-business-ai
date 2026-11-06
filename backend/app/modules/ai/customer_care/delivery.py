"""Recheck care-specific conditions at the actual outbox/publication claim boundary."""

from dataclasses import replace
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform import clock
from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.registry import DeliveryGuard

from . import policy
from .executor import activity, minimal_draft
from .models import CareDelivery
from .schemas import Kind


def guard(db: Session, ctx: Context, proposal_id: UUID) -> DeliveryGuard:
    receipt = db.scalar(
        select(CareDelivery).where(CareDelivery.business_id == ctx.business_id, CareDelivery.proposal_id == proposal_id)
    )
    if receipt is None:
        return DeliveryGuard()
    ctx = replace(
        ctx,
        proposal_id=proposal_id,
        causation_id=proposal_id,
        agent_key="customer_care",
        permissions=ctx.permissions | {"conversations.thread.read", "scheduling.appointment.read", "catalog.item.read"},
    )
    from app.modules.approvals import public as approvals

    authority = approvals.outbound_authority(db, ctx, proposal_id)
    if authority:
        ctx = replace(ctx, correlation_id=authority["correlation_id"], chain_depth=authority["chain_depth"] or 0)
    contact, reason, source = policy.contact(
        db,
        ctx,
        receipt.customer_id,
        receipt.kind,
        review=receipt.review_requested,
        source_message_id=receipt.source_message_id,
    )
    if not reason:
        reason = policy.ownership_reason(db, ctx, source, receipt.ai_generation)
    if not reason and receipt.kind == "recall":
        reason = policy.recall_reason(
            db, ctx, receipt.customer_id, receipt.service_id, receipt.appointment_id, receipt.due_month
        )
    if reason:
        activity(db, ctx, "skipped", reason)
        return DeliveryGuard(reason=reason)
    brief, settings = org.get_business_brief(db, ctx), org.get_settings(db, ctx)
    due = policy.allowed_time(max(clock.now(), receipt.not_before), brief.timezone, settings)
    if due > clock.now():
        activity(db, ctx, "scheduled", "customer_care.quiet_hours")
    subject = body = None
    if contact and (is_regulated() or org.customer_care_profile(db, ctx).get("avoid_medical_details") is True):
        minimal = minimal_draft(db, ctx, cast(Kind, receipt.kind), contact)
        subject, body = minimal.subject, minimal.body
    return DeliveryGuard(
        not_before=due,
        subject=subject,
        body=body,
        allow_human=receipt.kind == "complaint_reply",
        recipient=contact.email if contact else None,
    )
