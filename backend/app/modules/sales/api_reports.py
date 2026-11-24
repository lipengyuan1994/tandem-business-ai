"""Staff sales reports: sales by period, customer or item, and gross margin. Read-only."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require
from app.platform.reporting import date_range

from . import permissions as P
from . import reports
from .reminders import business_today
from .schemas import MarginReport, SalesReport

router = APIRouter()
ERRORS = error_responses(401, 403, 422)


@router.get(
    "/reports/sales",
    response_model=SalesReport,
    summary="Net sales by period, customer or item (defaults to this month in the business timezone)",
    responses=ERRORS,
)
def sales_report(
    date_from: date | None = None,
    date_to: date | None = None,
    group_by: Literal["period", "customer", "item"] = "period",
    period: Literal["day", "week", "month"] = "month",
    ctx: Context = Depends(require(P.DOCUMENT_READ)),
) -> SalesReport:
    with transaction(ctx) as db:
        r = date_range(date_from, date_to, business_today(db, ctx))
        return reports.sales(db, ctx, r, group_by, period)


@router.get(
    "/reports/margin",
    response_model=MarginReport,
    summary="Gross margin by item or kind at average purchase cost (needs purchasing read for costs)",
    responses=ERRORS,
)
def margin_report(
    date_from: date | None = None,
    date_to: date | None = None,
    group_by: Literal["item", "kind"] = "item",
    ctx: Context = Depends(require(P.DOCUMENT_READ)),
) -> MarginReport:
    with transaction(ctx) as db:
        r = date_range(date_from, date_to, business_today(db, ctx))
        return reports.margin(db, ctx, r, group_by)
