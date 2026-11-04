"""Time helpers. All stored times are UTC; local time is derived from IANA zones.

`now()` is injectable for tests through `set_clock` / `reset_clock` (see platform.testing).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_clock: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731


def now() -> datetime:
    return _clock()


def set_clock(fn: Callable[[], datetime]) -> None:
    global _clock
    _clock = fn


def reset_clock() -> None:
    set_clock(lambda: datetime.now(UTC))


def to_zone(dt: datetime, tz: str) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(ZoneInfo(tz))


def local_day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    """UTC start (inclusive) and end (exclusive) of a local calendar day. Handles 23/25-hour DST days."""
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time(0), zone)
    end = datetime.combine(day + timedelta(days=1), time(0), zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") + (f".{dt.microsecond:06d}" if dt.microsecond else "") + "Z"
