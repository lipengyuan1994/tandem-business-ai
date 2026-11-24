"""Staff purchasing reports: spend by supplier or period. Read-only."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends

from app.modules.org import public as org
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require
from app.platform.reporting import date_range, local_today

from . import permissions as P
from . import reports
from .schemas import SpendReport

router = APIRouter()


@router.get(
    "/reports/spend",
    response_model=SpendReport,
    summary="Ordered and received spend by supplier or period (defaults to this month in the business timezone)",
    responses=error_responses(401, 403, 422),
)
def spend_report(
    date_from: date | None = None,
    date_to: date | None = None,
    group_by: Literal["supplier", "period"] = "supplier",
    period: Literal["day", "week", "month"] = "month",
    ctx: Context = Depends(require(P.READ)),
) -> SpendReport:
    with transaction(ctx) as db:
        today = local_today(org.get_business_brief(db, ctx).timezone)
        return reports.spend(db, ctx, date_range(date_from, date_to, today), group_by, period)
