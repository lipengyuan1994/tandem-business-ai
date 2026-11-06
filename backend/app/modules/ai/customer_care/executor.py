"""One approved message → one durable subject receipt and one stoppable delivery."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.crm.public import CareContact
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.modules.sales import public as sales
from app.modules.scheduling import public as scheduling
from app.platform import clock, events
from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.errors import Invalid
from app.platform.events import DomainEventPayload
from app.platform.registry import ExecutionMeta, ExecutorSpec, PreviewLine

from . import policy, writer
from .models import CareDelivery
from .schemas import Draft, Kind, MessageFacts, SendMessage


class CareActivity(DomainEventPayload):
    event_type = "ai.customer_care_activity"
    status: str
    reason: str | None = None


def activity(db: Session, ctx: Context, status: str, reason: str | None = None) -> None:
    events.publish(db, ctx, CareActivity(status=status, reason=reason))


def describe(db: Session, ctx: Context, p: SendMessage) -> list[PreviewLine]:
    return [
        PreviewLine(f"Customer Care: {p.kind}", None, p.body),
        PreviewLine("Earliest send", None, p.not_before.isoformat()),
    ]


def validate(db: Session, ctx: Context, p: SendMessage) -> None:
    crm.customer_care_contact(db, ctx, p.customer_id)
    if p.appointment_id:
        a = scheduling.get_appointment(db, ctx, p.appointment_id, lock=False)
        day = a.ends_at.astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()
        if (
            a.customer_id != p.customer_id
            or a.service_id != p.service_id
            or day != p.service_date
            or a.status != "completed"
        ):
            raise Invalid("customer_care.facts_changed")
        if p.kind == "recall":
            if reason := policy.recall_reason(db, ctx, p.customer_id, p.service_id, p.appointment_id, p.due_month):
                raise Invalid(reason)
    if p.source_message_id:
        source = conversations.get_message_context(db, ctx, p.source_message_id)
        if source.customer_id != p.customer_id:
            raise Invalid("customer_care.source_unavailable")
    if p.invoice_id:
        sales.validate_care_invoice(db, ctx, p.invoice_id, p.customer_id, p.appointment_id)


def autonomy_reasons(db: Session, ctx: Context, p: SendMessage) -> list[str]:
    if p.kind == "complaint_reply":
        return ["customer_care.complaint_requires_human"]
    _, reason, _ = policy.contact(
        db, ctx, p.customer_id, p.kind, review=p.review_requested, source_message_id=p.source_message_id
    )
    return [reason] if reason else []


def minimal_draft(db: Session, ctx: Context, kind: Kind, contact: CareContact) -> Draft:
    brief = org.get_business_brief(db, ctx)
    return writer.template(
        kind, MessageFacts(business_name=brief.name, language=contact.preferred_language or brief.locale), minimal=True
    )


def run(db: Session, ctx: Context, p: SendMessage, meta: ExecutionMeta) -> dict[str, Any]:
    contact, reason, source = policy.contact(
        db, ctx, p.customer_id, p.kind, review=p.review_requested, source_message_id=p.source_message_id
    )
    existing = db.scalar(
        select(CareDelivery.id).where(
            CareDelivery.business_id == ctx.business_id, CareDelivery.domain_key == p.domain_key
        )
    )
    if existing:
        reason = "customer_care.duplicate"
    if not reason:
        reason = policy.ownership_reason(db, ctx, source, p.ai_generation)
    if not reason and p.kind == "recall":
        reason = policy.recall_reason(db, ctx, p.customer_id, p.service_id, p.appointment_id, p.due_month)
    if reason:
        activity(db, ctx, "skipped", reason)
        return {"status": "skipped", "reason": reason}
    assert contact is not None
    brief, settings = org.get_business_brief(db, ctx), org.get_settings(db, ctx)
    draft = Draft(subject=p.subject, body=p.body)
    if is_regulated() or org.customer_care_profile(db, ctx).get("avoid_medical_details") is True:
        draft = minimal_draft(db, ctx, p.kind, contact)
    due = policy.allowed_time(
        max(p.not_before, clock.now() + timedelta(seconds=settings.outbound_send_delay_seconds)),
        brief.timezone,
        settings,
    )
    if due > max(p.not_before, clock.now() + timedelta(seconds=settings.outbound_send_delay_seconds)):
        activity(db, ctx, "scheduled", "customer_care.quiet_hours")
    receipt = CareDelivery(
        business_id=ctx.business_id,
        customer_id=p.customer_id,
        domain_key=p.domain_key,
        proposal_id=meta.proposal_id,
        kind=p.kind,
        review_requested=p.review_requested,
        due_month=p.due_month,
        ai_generation=p.ai_generation,
        not_before=due,
        source_message_id=p.source_message_id,
        appointment_id=p.appointment_id,
        service_id=p.service_id,
    )
    if source and source.channel == "web_chat":
        send_ctx = replace(
            ctx, permissions=ctx.permissions | {"conversations.thread.write", "conversations.thread.read"}
        )
        message = conversations.send_care_reply(
            db,
            send_ctx,
            source.conversation_id,
            draft.body,
            source_message_id=p.source_message_id,
            not_before=due,
            proposal_id=meta.proposal_id,
            ai_generation=p.ai_generation,
        )
        receipt.message_id = message.id
        result = {"message_id": str(message.id), "status": "scheduled"}
    else:
        receipt.outbox_id = notifications.enqueue_message(
            db,
            ctx,
            channel=notifications.email_channel(),
            recipient=contact.email,
            template_key="customer_care",
            variables=draft.model_dump(),
            idempotency_key=f"care:{ctx.business_id}:{p.domain_key}",
            related_type="customer",
            related_id=p.customer_id,
            proposal_id=meta.proposal_id,
            not_before=due,
        )
        result = {"outbox_id": str(receipt.outbox_id), "status": "scheduled"}
    db.add(receipt)
    db.flush()
    activity(db, ctx, "scheduled")
    return result


SPEC = ExecutorSpec(
    "customer_care.send_message",
    SendMessage,
    "conversations.proposal.approve",
    describe,
    validate,
    run,
    autonomy_reasons=autonomy_reasons,
)
