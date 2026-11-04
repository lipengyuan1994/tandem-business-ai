"""/api/v1/dashboard: read-only aggregation through other modules' public functions. Owns no tables."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.modules.approvals import public as approvals
from app.modules.conversations import public as conversations
from app.modules.notifications import public as notifications
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require_session

from .service import TodaySummary as TodayOut
from .service import today_summary

router = APIRouter()


class CountersOut(BaseModel):
    inbox_waiting_on_staff: int
    approvals_i_can_decide: int
    unread_notifications: int


@router.get(
    "/today",
    response_model=TodayOut,
    summary="Today: each section only if you may read that module",
    responses=error_responses(401, 403),
)
def today(ctx: Context = Depends(require_session())) -> TodayOut:
    with transaction(ctx) as db:
        return today_summary(db, ctx)


@router.get(
    "/counters",
    response_model=CountersOut,
    summary="Navigation badges (poll every 30 s)",
    responses=error_responses(401),
)
def counters(ctx: Context = Depends(require_session())) -> CountersOut:
    if ctx.business_id is None:
        return CountersOut(inbox_waiting_on_staff=0, approvals_i_can_decide=0, unread_notifications=0)
    with transaction(ctx) as db:
        inbox = (
            conversations.count_open_by_status(db, ctx).get("waiting_on_staff", 0)
            if ctx.has("conversations.thread.read")
            else 0
        )
        pending = (
            approvals.count_pending(db, ctx, for_permissions=ctx.permissions)
            if ctx.has("approvals.proposal.read")
            else 0
        )
        return CountersOut(
            inbox_waiting_on_staff=inbox,
            approvals_i_can_decide=pending,
            unread_notifications=notifications.unread_count(db, ctx),
        )
