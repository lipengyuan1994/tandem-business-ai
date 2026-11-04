"""Usage and budgets (contracts §12). The budget counts input + output tokens of the business's UTC day; usage rows are
updated after every model call (recording.py), so a check also sees runs still in progress."""

from __future__ import annotations

from datetime import UTC, date, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.platform import clock
from app.platform.context import Context

from .errors import AiError
from .models import AiUsageDaily
from .permissions import SETTINGS_READ
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
    """First crossing of 80 % per UTC day: notify holders of ai.settings.read (at most once a day, also under
    concurrent runs: the row update that sets `notified_80_at` is the guard)."""
    today = usage_today(db, ctx)
    if today.percent < NOTICE_PERCENT:
        return
    claimed = db.execute(
        update(AiUsageDaily)
        .where(
            AiUsageDaily.business_id == ctx.business_id,
            AiUsageDaily.day == utc_today(),
            AiUsageDaily.notified_80_at.is_(None),
        )
        .values(notified_80_at=clock.now())
        .returning(AiUsageDaily.id)
    ).first()
    if claimed is None:
        return
    users = [u.id for u in identity.list_users_with_permission(db, ctx, SETTINGS_READ)]
    notifications.notify_users(
        db, ctx, users, kind="ai.budget_80",
        title=f"AI usage reached {min(today.percent, 100)} % of today's budget",
        body="AI features stop for the rest of the UTC day when the budget is used up. "
        "You can change the daily budget in the AI settings.",
    )  # fmt: skip


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
