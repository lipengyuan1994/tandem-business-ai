"""Read-only Today aggregation, in the caller's transaction."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.conversations import public as conversations
from app.modules.inventory import public as inventory
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.modules.scheduling import public as scheduling
from app.platform import clock
from app.platform.clock import local_day_bounds
from app.platform.context import Context


class PendingItem(BaseModel):
    id: uuid.UUID
    title: str
    action_type: str
    origin_kind: str
    created_at: datetime


class ApprovalsSection(BaseModel):
    can_decide: int
    top: list[PendingItem]


class TodaySummary(BaseModel):
    local_date: date
    timezone: str
    agenda: list[scheduling.AppointmentOut] | None = None
    conversations: dict[str, int] | None = None
    approvals: ApprovalsSection | None = None
    low_stock: list[inventory.ReorderSuggestion] | None = None
    delivery_issues: int | None = None


def today_summary(db: Session, ctx: Context) -> TodaySummary:
    business = org.get_business_brief(db, ctx)
    tz = business.timezone
    local_date = clock.now().astimezone(ZoneInfo(tz)).date()
    start, end = local_day_bounds(local_date, tz)
    out = TodaySummary(local_date=local_date, timezone=tz)
    if ctx.has("scheduling.appointment.read"):
        out.agenda = scheduling.agenda(db, ctx, start, end)
    if ctx.has("conversations.thread.read"):
        out.conversations = conversations.count_open_by_status(db, ctx)
    if ctx.has("approvals.proposal.read"):
        out.approvals = ApprovalsSection(
            can_decide=approvals.count_pending(db, ctx, for_permissions=ctx.permissions),
            top=[PendingItem(**p) for p in approvals.top_pending(db, ctx)],
        )
    if ctx.has("inventory.stock.read"):
        out.low_stock = inventory.list_below_reorder(db, ctx)
    if ctx.has("notifications.outbox.read"):
        counts = notifications.outbox_status_counts(db, ctx)
        out.delivery_issues = counts.get("unknown", 0) + counts.get("failed", 0)
    return out
