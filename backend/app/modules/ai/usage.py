"""Usage and notices (contracts §12). Counters include committed in-flight reservations.

Financial and token admission is enforced in reservations.py before each provider attempt.
"""

from __future__ import annotations

from datetime import UTC, date, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform import clock
from app.platform.config import get_settings
from app.platform.context import Context

from .budget_models import CostPeriod
from .budget_notices import notify
from .errors import AiError
from .models import AiUsageDaily
from .schemas import UsageDayOut, UsageToday
from .settings import ai_settings

NOTICE_PERCENT = 80


def utc_today() -> date:
    return clock.now().astimezone(UTC).date()


def effective_budget(db: Session, ctx: Context) -> int:
    """The business's `ai_daily_token_budget`, or AI_DAILY_TOKEN_BUDGET_PER_BUSINESS when it has none."""
    own = org.get_settings(db, ctx).ai_daily_token_budget
    if own is not None:
        return own
    return ai_settings().daily_token_budget_per_business


def _row(db: Session, ctx: Context, day: date) -> AiUsageDaily | None:
    return db.scalar(select(AiUsageDaily).where(AiUsageDaily.business_id == ctx.business_id, AiUsageDaily.day == day))


def _percent(used: int, budget: int) -> int:
    if budget <= 0:
        return 100
    return 100 * used // budget


def usage_today(db: Session, ctx: Context) -> UsageToday:
    row = _row(db, ctx, utc_today())
    used_in, used_out, cost = (row.input_tokens, row.output_tokens, row.cost_micros) if row else (0, 0, 0)
    budget = effective_budget(db, ctx)
    return UsageToday(
        input_tokens=used_in,
        output_tokens=used_out,
        cost_micros=cost,
        budget_tokens=budget,
        percent=_percent(used_in + used_out, budget),
    )


def check_budget(db: Session, ctx: Context) -> None:
    """Raises AiError("ai.budget_exceeded") at ≥ 100 % of today's budget."""
    if usage_today(db, ctx).percent >= 100:
        raise AiError("ai.budget_exceeded")


def maybe_notify_80_percent(db: Session, ctx: Context) -> None:
    """80%/100% per scope/period: in-app plus an optional operator outbox, atomically deduplicated."""
    today = usage_today(db, ctx)
    scopes = [("business", utc_today(), today.percent)]
    s = get_settings()
    for kind, period, limit in (
        ("day", utc_today(), s.ai_daily_cost_limit_usd),
        ("month", utc_today().replace(day=1), s.ai_monthly_cost_limit_usd),
    ):
        cost = (
            db.scalar(select(CostPeriod.cost_micros).where(CostPeriod.kind == kind, CostPeriod.period == period)) or 0
        )
        scopes.append((f"installation_{kind}", period, _percent(cost, int(limit * 1_000_000))))
    for scope, period, percent in scopes:
        for threshold in (80, 100):
            if percent >= threshold:
                notify(
                    db,
                    ctx,
                    scope,
                    period,
                    threshold,
                    "Usage includes in-flight reservations. Calls that cannot fit are refused. "
                    "Failed attempts without reported usage retain their full allowance.",
                )
    if today.percent >= NOTICE_PERCENT:
        db.execute(
            update(AiUsageDaily)
            .where(
                AiUsageDaily.business_id == ctx.business_id,
                AiUsageDaily.day == utc_today(),
                AiUsageDaily.notified_80_at.is_(None),
            )
            .values(notified_80_at=clock.now())
        )


def usage_days(db: Session, ctx: Context, days: int = 30) -> list[UsageDayOut]:
    """The last `days` UTC days, oldest first, today last; zeros for days without use."""
    last = utc_today()
    first = last - timedelta(days=days - 1)
    rows = {
        r.day: r
        for r in db.scalars(
            select(AiUsageDaily).where(
                AiUsageDaily.business_id == ctx.business_id, AiUsageDaily.day >= first, AiUsageDaily.day <= last
            )
        )
    }
    out = []
    for i in range(days):
        day = first + timedelta(days=i)
        r = rows.get(day)
        out.append(
            UsageDayOut(
                day=day,
                input_tokens=r.input_tokens if r else 0,
                output_tokens=r.output_tokens if r else 0,
                cost_micros=r.cost_micros if r else 0,
                runs=r.runs if r else 0,
            )
        )
    return out


__all__ = [
    "UsageToday",
    "check_budget",
    "effective_budget",
    "maybe_notify_80_percent",
    "usage_days",
    "usage_today",
    "utc_today",
]
