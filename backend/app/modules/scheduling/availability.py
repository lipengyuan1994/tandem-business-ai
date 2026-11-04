"""Deterministic slot finder (docs/roadmap/modules/M06-scheduling.md#availability-algorithm-find_slots--deterministic).

All interval maths happens on timezone-aware datetimes. Local wall-clock times (opening hours, availability rules) are
converted with zoneinfo for each calendar day, so DST changes are handled per day. Starts are aligned to the slot
granularity in *local* time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class DayRules:
    open_hours: list[tuple[time, time]]  # business opening hours for the weekday
    resource_hours: list[tuple[time, time]]  # the resource's availability rules for that date


def _local(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, t, tz).astimezone(UTC)


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out = []
    for s1, e1 in a:
        for s2, e2 in b:
            s, e = max(s1, s2), min(e1, e2)
            if s < e:
                out.append((s, e))
    return merge(out)


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def subtract(base: list[Interval], remove: list[Interval]) -> list[Interval]:
    result = merge(base)
    for rs, re_ in merge(remove):
        nxt: list[Interval] = []
        for s, e in result:
            if re_ <= s or rs >= e:
                nxt.append((s, e))
                continue
            if s < rs:
                nxt.append((s, rs))
            if re_ < e:
                nxt.append((re_, e))
        result = nxt
    return result


def working_intervals(
    day: date, tz: ZoneInfo, rules: DayRules, closed: list[Interval], extra: list[Interval]
) -> list[Interval]:
    """(opening hours ∩ resource rules) − closed exceptions ∪ extra-open exceptions, for one local day, in UTC."""
    open_ = merge([(_local(day, s, tz), _local(day, e, tz)) for s, e in rules.open_hours])
    res = merge([(_local(day, s, tz), _local(day, e, tz)) for s, e in rules.resource_hours])
    day_start, day_end = _local(day, time(0), tz), _local(day + timedelta(days=1), time(0), tz)
    extra_today = intersect(extra, [(day_start, day_end)])
    return merge(subtract(intersect(open_, res), closed) + extra_today)


def align_up(t: datetime, tz: ZoneInfo, granularity_min: int) -> datetime:
    """Round up to the next local-time boundary of `granularity_min` minutes."""
    local = t.astimezone(tz)
    minutes = local.hour * 60 + local.minute
    extra = (-minutes) % granularity_min
    if extra == 0 and local.second == 0 and local.microsecond == 0:
        return t
    if extra == 0:
        extra = granularity_min
    aligned = (local.replace(second=0, microsecond=0) + timedelta(minutes=extra)).replace(fold=local.fold)
    return aligned.astimezone(UTC)


def slot_starts(
    free: list[Interval],
    *,
    tz: ZoneInfo,
    granularity_min: int,
    block: timedelta,
    not_before: datetime,
    latest_start: datetime,
) -> list[datetime]:
    """Starts where [start, start + block) fits in a free interval and not_before <= start < latest_start (half-open).

    The query's `latest` bounds the *start* ("Tuesday morning" = starting before noon), not the end.
    """
    out = []
    step = timedelta(minutes=granularity_min)
    for s, e in free:
        start = align_up(max(s, not_before), tz, granularity_min)
        while start + block <= e and start < latest_start:
            out.append(start)
            start += step
    return out


def local_label(t: datetime, tz: ZoneInfo) -> str:
    local = t.astimezone(tz)
    return f"{local:%a %d %b %H:%M} {local.tzname()}"
