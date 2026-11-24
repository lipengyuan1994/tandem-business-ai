"""Shared helpers for read-only business reports: date ranges, period buckets and integer ratios.

Reports aggregate one module's own tables (see each module's `reports.py`). Dates are business-local calendar days;
callers turn them into UTC bounds with `clock.local_day_bounds` when they filter timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from app.platform import clock
from app.platform.clock import local_day_bounds
from app.platform.errors import Invalid

Period = Literal["day", "week", "month"]
MAX_RANGE_DAYS = 3 * 366
"""Longest accepted range, so a daily report cannot ask for an unbounded number of rows."""


@dataclass(frozen=True, slots=True)
class DateRange:
    date_from: date
    date_to: date

    def utc_bounds(self, tz: str) -> tuple[datetime, datetime]:
        """UTC start (inclusive) of `date_from` and end (exclusive) of `date_to` in the business time zone."""
        return local_day_bounds(self.date_from, tz)[0], local_day_bounds(self.date_to, tz)[1]


def local_today(tz: str) -> date:
    return clock.now().astimezone(ZoneInfo(tz)).date()


def date_range(date_from: date | None, date_to: date | None, today: date) -> DateRange:
    """Defaults: the first of `today`'s month through `today`. Rejects reversed or over-long ranges."""
    end = date_to if date_to is not None else today
    start = date_from if date_from is not None else end.replace(day=1)
    if start > end:
        raise Invalid("reports.invalid_range", detail="The start date must be on or before the end date")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise Invalid("reports.range_too_long", detail=f"Choose a range shorter than {MAX_RANGE_DAYS} days")
    return DateRange(start, end)


def period_start(day: date, period: Period) -> date:
    """First day of the bucket holding `day`. Weeks start on Monday (ISO 8601)."""
    if period == "day":
        return day
    if period == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def basis_points(part: int, whole: int) -> int | None:
    """`part / whole` in hundredths of a percent, rounded half up; None when `whole` is not positive."""
    if whole <= 0:
        return None
    return int((Decimal(part) * 10000 / Decimal(whole)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def round_minor(value: Decimal) -> int:
    """Money in minor units from an exact decimal product (half up, like document totals)."""
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
