from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.platform.errors import Invalid
from app.platform.reporting import MAX_RANGE_DAYS, DateRange, basis_points, date_range, period_start


def test_period_start_buckets_by_iso_week_and_month() -> None:
    assert period_start(date(2026, 11, 1), "week") == date(2026, 10, 26)  # Sunday belongs to Monday's week
    assert period_start(date(2026, 11, 2), "week") == date(2026, 11, 2)
    assert period_start(date(2026, 2, 28), "month") == date(2026, 2, 1)
    assert period_start(date(2026, 2, 28), "day") == date(2026, 2, 28)


def test_date_range_defaults_and_rejections() -> None:
    today = date(2026, 11, 24)
    assert date_range(None, None, today) == DateRange(date(2026, 11, 1), today)
    assert date_range(None, date(2026, 3, 9), today) == DateRange(date(2026, 3, 1), date(2026, 3, 9))
    assert date_range(date(2026, 5, 5), None, today) == DateRange(date(2026, 5, 5), today)
    with pytest.raises(Invalid, match="reports.invalid_range"):
        date_range(date(2026, 5, 2), date(2026, 5, 1), today)
    start = date(2023, 1, 1)
    assert date_range(start, date.fromordinal(start.toordinal() + MAX_RANGE_DAYS - 1), today).date_from == start
    with pytest.raises(Invalid, match="reports.range_too_long"):
        date_range(start, date.fromordinal(start.toordinal() + MAX_RANGE_DAYS), today)


def test_utc_bounds_cover_whole_local_days_across_dst() -> None:
    start, end = DateRange(date(2026, 11, 1), date(2026, 11, 1)).utc_bounds("America/New_York")
    assert start == datetime(2026, 11, 1, 4, tzinfo=UTC)  # EDT midnight
    assert end == datetime(2026, 11, 2, 5, tzinfo=UTC)  # EST midnight: a 25-hour day


def test_basis_points_round_half_up_and_need_a_positive_base() -> None:
    assert basis_points(3000, 6300) == 4762
    assert basis_points(1, 8) == 1250
    assert basis_points(-700, 1800) == -3889
    assert basis_points(5, 0) is None and basis_points(5, -10) is None
