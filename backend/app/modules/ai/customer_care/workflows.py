"""Existing event/job/run/proposal path. Discovery does not share one chain across a business."""

from __future__ import annotations

import calendar
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.catalog import public as catalog
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.org import public as org
from app.modules.scheduling import public as scheduling
from app.platform import clock, jobs
from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict

from .. import effects, recording
from ..agent.contexts import background_ctx_factory
from ..agent.prompts import load_prompt
from ..gateway.router import Router
from ..team_settings import enabled
from . import policy, writer
from .executor import activity
from .schemas import Kind, MessageFacts, SendMessage


def add_months(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def lock_subject(db: Session, ctx: Context, key: str) -> None:
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"customer-care:{ctx.business_id}:{key}"},
    )


def completed_trigger(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    from ..subscribers import _domain_context

    ctx = _domain_context(ctx, event_id)
    if not enabled(db, ctx, "customer_care", lock=True):
        return
    delay = org.get_settings(db, ctx).checkin_delay_days
    if not delay:
        return
    ident = uuid.UUID(str(payload["appointment_id"]))
    completed = datetime.fromisoformat(str(payload["completed_at"])) if payload.get("completed_at") else clock.now()
    if completed.tzinfo is None:
        completed = clock.now()
    jobs.enqueue(
        db,
        "ai.customer_care_checkin",
        {"appointment_id": str(ident)},
        business_id=ctx.business_id,
        run_at=completed + timedelta(days=delay),
        dedupe_key=f"care-checkin:{ctx.business_id}:{ident}",
        ctx=ctx,
    )


def record_skip(ctx: Context, key: str, reason: str) -> None:
    result = recording.record_early_result(
        ctx,
        recording.RunMeta(
            use_case="customer_care",
            prompt=load_prompt("customer_care_message", 1),
            trigger_type="job",
            trigger_key=key,
        ),
        needs_human=False,
        result={"status": "skipped", "reason": reason},
    )
    with transaction(ctx) as db:
        activity(db, replace(ctx, causation_id=result.run_id), "skipped", reason)


def _prepare(
    business_id: uuid.UUID,
    kind: Kind,
    *,
    appointment_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    source_message_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    due_month: str | None = None,
    router: Router | None = None,
) -> uuid.UUID | None:
    factory = background_ctx_factory(business_id, use_case="customer_care")
    ctx = factory(uuid.uuid4())
    with transaction(ctx) as db:
        if not enabled(db, ctx, "customer_care", lock=True):
            return None
        settings, brief = org.get_settings(db, ctx), org.get_business_brief(db, ctx)
        approvals.check_ai_proposal_allowed(db, ctx, "customer_care.send_message")
        if kind == "checkin" and not settings.checkin_delay_days:
            return None
        appointment = scheduling.get_appointment(db, ctx, appointment_id, lock=False) if appointment_id else None
        if appointment is not None:
            customer_id = appointment.customer_id
        if customer_id is None:
            return None
        p = SendMessage(
            customer_id=customer_id,
            kind=kind,
            subject="Pending draft",
            body="Pending draft",
            appointment_id=appointment_id,
            service_id=appointment.service_id if appointment else None,
            service_date=appointment.ends_at.astimezone(ZoneInfo(brief.timezone)).date() if appointment else None,
            source_message_id=source_message_id,
            invoice_id=invoice_id,
            due_month=due_month,
            not_before=clock.now(),
        )
        key = p.domain_key
        effect_key = effects.effect_key("customer_care", {"domain_key": key})
        if prior := effects.find_effect(db, ctx, effect_key):
            return prior.ref_id
        contact, reason, _ = policy.contact(db, ctx, customer_id, kind, source_message_id=source_message_id)
        if appointment and appointment.status != "completed":
            reason = "customer_care.facts_changed"
        service = catalog.get_item(db, ctx, appointment.service_id) if appointment else None
        if kind == "recall" and appointment and service:
            last = scheduling.latest_completed_appointment(db, ctx, customer_id, service.id)
            if (
                last is None
                or last.id != appointment.id
                or scheduling.has_future_service_appointment(db, ctx, customer_id, service.id)
            ):
                reason = "customer_care.future_appointment"
            if service.status != "active" or not service.recall_interval_months:
                reason = "customer_care.recall_off"
            else:
                assert p.service_date is not None
                due = add_months(p.service_date, service.recall_interval_months)
                if due.strftime("%Y-%m") != due_month or clock.now().astimezone(
                    ZoneInfo(brief.timezone)
                ).date() < due - timedelta(days=14):
                    reason = "customer_care.not_due"
        profile = org.customer_care_profile(db, ctx)
        review_url = profile.get("review_url") if kind in {"checkin", "review_request"} else None
        if kind == "checkin" and crm.current_consent(db, ctx, customer_id, "email", "marketing").status != "granted":
            review_url = None
        p.review_requested = bool(review_url) or kind == "review_request"
        facts = MessageFacts(
            first_name=contact.first_name if contact else "",
            service_name=service.name if service else "",
            service_date=p.service_date.isoformat() if p.service_date else "",
            business_name=brief.name,
            review_url=review_url if isinstance(review_url, str) else None,
            booking_url=profile.get("booking_url") if isinstance(profile.get("booking_url"), str) else None,
            language=(contact.preferred_language if contact else None) or brief.locale,
            tone=settings.customer_care_tone,
        )
        minimal = is_regulated() or profile.get("avoid_medical_details") is True
    if reason:
        record_skip(ctx, key, reason)
        return None
    run_id, draft = writer.write(factory, kind, facts, key, minimal=minimal, router=router)
    ctx = replace(factory(run_id), causation_id=run_id)
    with transaction(ctx) as db:
        if not enabled(db, ctx, "customer_care", lock=True):
            return None
        approvals.check_ai_proposal_allowed(db, ctx, "customer_care.send_message")
        # Customer before subject locks, same order as concurrent executions and complaint creation.
        _, reason, source = policy.contact(
            db, ctx, customer_id, kind, review=p.review_requested, source_message_id=source_message_id
        )
        lock_subject(db, ctx, key)
        if prior := effects.find_effect(db, ctx, effect_key):
            return prior.ref_id
        if reason:
            activity(db, ctx, "skipped", reason)
            return None
        if source:
            conv = conversations.lock_for_customer(db, ctx, source.conversation_id)
            p.ai_generation = conv.ai_generation
        p.subject, p.body = draft.subject, draft.body
        evidence = (
            [approvals.Evidence(kind="message", ref_type="message", ref_id=source_message_id)]
            if source_message_id
            else []
        )
        result = approvals.create_proposal(
            db,
            ctx,
            action_type="customer_care.send_message",
            payload=p.model_dump(mode="json"),
            title=f"Customer Care: {kind}",
            evidence=evidence,
            conversation_id=source.conversation_id if source else None,
        )
        effects.record_effect(
            db, ctx, run_id=run_id, trigger_key=key, effect_key=effect_key, kind="proposal", ref_id=result.id
        )
        return result.id


def prepare(
    business_id: uuid.UUID,
    kind: Kind,
    *,
    appointment_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    source_message_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    due_month: str | None = None,
    router: Router | None = None,
) -> uuid.UUID | None:
    try:
        return _prepare(
            business_id,
            kind,
            appointment_id=appointment_id,
            customer_id=customer_id,
            source_message_id=source_message_id,
            invoice_id=invoice_id,
            due_month=due_month,
            router=router,
        )
    except Conflict as exc:
        if exc.code == "approvals.autonomy_off":
            return None
        raise
    except writer.TemplateUnavailable:
        factory = background_ctx_factory(business_id, use_case="customer_care")
        record_skip(
            factory(uuid.uuid4()), f"{kind}:{customer_id or appointment_id}", "customer_care.language_unavailable"
        )
        return None


def checkin_job(ctx: Context, payload: dict[str, Any]) -> None:
    assert ctx.business_id is not None
    prepare(ctx.business_id, "checkin", appointment_id=uuid.UUID(str(payload["appointment_id"])))


def recall_scan(ctx: Context, payload: dict[str, Any]) -> None:
    assert ctx.business_id is not None
    factory = background_ctx_factory(ctx.business_id, use_case="customer_care", parent=ctx)
    read = factory(uuid.uuid4())
    with transaction(read) as db:
        services = catalog.recall_services(db, read)
        brief = org.get_business_brief(db, read)
    day = clock.now().astimezone(ZoneInfo(brief.timezone)).date()
    for service in services:
        assert service.recall_interval_months is not None
        after = None
        while True:
            with transaction(read) as db:
                if not enabled(db, read, "customer_care", lock=True):
                    return
                appointments = scheduling.completed_service_page(db, read, service.id, after=after)
                for appointment in appointments:
                    due = add_months(
                        appointment.ends_at.astimezone(ZoneInfo(brief.timezone)).date(), service.recall_interval_months
                    )
                    if day < due - timedelta(days=14):
                        continue
                    domain = f"recall:{appointment.customer_id}:{service.id}:{due:%Y-%m}"
                    root = replace(
                        ctx,
                        correlation_id=uuid.uuid5(uuid.NAMESPACE_URL, f"care:{ctx.business_id}:{domain}"),
                        agent_key=None,
                        causing_agent_key=None,
                        chain_depth=0,
                    )
                    _, reason, _ = policy.contact(db, read, appointment.customer_id, "recall")
                    if not reason and scheduling.has_future_service_appointment(
                        db, read, appointment.customer_id, service.id
                    ):
                        reason = "customer_care.future_appointment"
                    if effects.find_effect(db, read, effects.effect_key("customer_care", {"domain_key": domain})):
                        reason = "customer_care.duplicate"
                    if reason:
                        activity(db, replace(root, agent_key="customer_care"), "skipped", reason)
                        continue
                    jobs.enqueue(
                        db,
                        "ai.customer_care_recall_subject",
                        {"appointment_id": str(appointment.id), "due_month": due.strftime("%Y-%m")},
                        business_id=ctx.business_id,
                        dedupe_key=f"care-recall:{ctx.business_id}:{domain}:{day}",
                        ctx=root,
                    )
            if len(appointments) < 500:
                break
            after = appointments[-1].customer_id


def recall_job(ctx: Context, payload: dict[str, Any]) -> None:
    assert ctx.business_id is not None
    prepare(
        ctx.business_id,
        "recall",
        appointment_id=uuid.UUID(str(payload["appointment_id"])),
        due_month=str(payload["due_month"]),
    )
