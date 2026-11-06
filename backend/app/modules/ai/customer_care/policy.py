"""Exact eligibility and business-local quiet hours; no inference."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.org import public as org
from app.modules.scheduling import public as scheduling
from app.platform import clock
from app.platform.context import Context
from app.platform.errors import NotFound

from .models import CareCase


def allowed_time(at: datetime, timezone: str, settings: org.BusinessSettings) -> datetime:
    local = at.astimezone(ZoneInfo(timezone))
    start, end = settings.customer_care_quiet_start_hour, settings.customer_care_quiet_end_hour
    quiet = (local.hour >= start or local.hour < end) if start > end else start <= local.hour < end
    if start == end or not quiet:
        return at
    target = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if start > end and local.hour >= start:
        target += timedelta(days=1)
    # Round trip resolves missing wall hours at DST transitions conservatively.
    return target.astimezone(UTC)


def contact(
    db: Session,
    ctx: Context,
    customer_id: uuid.UUID,
    kind: str,
    *,
    review: bool = False,
    source_message_id: uuid.UUID | None = None,
) -> tuple[crm.CareContact | None, str | None, conversations.MessageContext | None]:
    value = crm.customer_care_contact(db, ctx, customer_id)
    if value is None or value.status != "active":
        return (
            value,
            "customer_care.customer_erased"
            if value is None or value.status in {"erased", "restricted"}
            else "customer_care.customer_inactive",
            None,
        )
    if value.preferred_contact_channel == "none":
        return value, "customer_care.contact_preference", None
    source = None
    if source_message_id:
        try:
            source = conversations.get_message_context(db, ctx, source_message_id)
        except NotFound:
            return value, "customer_care.source_unavailable", None
        if source.customer_id != customer_id or source.author_kind not in {"visitor", "customer"}:
            return value, "customer_care.source_unavailable", None
    web = source is not None and source.channel == "web_chat"
    if value.preferred_contact_channel == "web_chat" and not web:
        return value, "customer_care.contact_preference", None
    # Source web chat remains in that thread; consent is checked by its existing public boundary.
    if not web:
        if not value.email or crm.current_consent(db, ctx, customer_id, "email", "service_updates").status != "granted":
            return value, "customer_care.no_consent", None
        if (review or kind == "review_request") and crm.current_consent(
            db, ctx, customer_id, "email", "marketing"
        ).status != "granted":
            return value, "customer_care.no_consent", None
    if kind != "complaint_reply" and not crm.proactive_service_consent(db, ctx, customer_id):
        return value, "customer_care.no_consent", source
    if kind != "complaint_reply" and db.scalar(
        select(CareCase.id)
        .where(CareCase.business_id == ctx.business_id, CareCase.customer_id == customer_id, CareCase.status == "open")
        .limit(1)
    ):
        return value, "customer_care.dispute", source
    return value, None, source


def ownership_reason(
    db: Session, ctx: Context, source: conversations.MessageContext | None, generation: int | None
) -> str | None:
    """Serialize staff intervention with execution/claim on both mail and web paths."""
    if source is not None:
        conv = conversations.lock_for_customer(db, ctx, source.conversation_id)
        if generation is None or conv.ai_generation != generation:
            return "conversations.ai_paused"
    return None


def recall_reason(
    db: Session,
    ctx: Context,
    customer_id: uuid.UUID,
    service_id: uuid.UUID | None,
    appointment_id: uuid.UUID | None,
    due_month: str | None,
) -> str | None:
    """Same current service/date eligibility at proposal, execution and actual delivery."""
    from .workflows import add_months

    if service_id is None:
        return "customer_care.recall_off"
    try:
        service = catalog.get_item(db, ctx, service_id)
    except NotFound:
        return "customer_care.recall_off"
    if service.status != "active" or not service.recall_interval_months:
        return "customer_care.recall_off"
    last = scheduling.latest_completed_appointment(db, ctx, customer_id, service_id)
    if (
        last is None
        or last.id != appointment_id
        or scheduling.has_future_service_appointment(db, ctx, customer_id, service_id)
    ):
        return "customer_care.future_appointment"
    zone = ZoneInfo(org.get_business_brief(db, ctx).timezone)
    due = add_months(last.ends_at.astimezone(zone).date(), service.recall_interval_months)
    if due.strftime("%Y-%m") != due_month or clock.now().astimezone(zone).date() < due - timedelta(days=14):
        return "customer_care.not_due"
    return None
