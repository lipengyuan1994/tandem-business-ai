"""Team counters join the existing reservation transaction; conditional row updates bound concurrency."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.modules.notifications import public as notifications
from app.platform import clock
from app.platform.context import Context
from app.platform.db import transaction

from .agents import AGENTS
from .chains import require_enabled
from .errors import AiError
from .models import AiRun
from .team_models import AgentUsageDaily, ChainBudget
from .team_settings import settings

MAX_CALLS = 30
MAX_ACTIONS = 10
MAX_COST_MICROS = 500000


def daily_spend(db: Session, ctx: Context, day: date) -> int:
    recorded = db.scalar(
        select(AgentUsageDaily.cost_micros).where(
            AgentUsageDaily.business_id == ctx.business_id,
            AgentUsageDaily.agent_key == ctx.agent_key,
            AgentUsageDaily.day == day,
        )
    )
    if recorded is not None:
        return recorded
    since = datetime.combine(day, datetime.min.time(), UTC)
    return int(
        db.scalar(
            select(func.coalesce(func.sum(AiRun.cost_micros), 0)).where(
                AiRun.business_id == ctx.business_id,
                AiRun.agent_key.is_(None),
                AiRun.use_case.in_(AGENTS[ctx.agent_key].use_cases if ctx.agent_key else ()),
                AiRun.started_at >= since,
                AiRun.started_at < since + timedelta(days=1),
            )
        )
        or 0
    )


def check_daily_limit(db: Session, ctx: Context) -> None:
    if ctx.agent_key is None:
        return
    config = settings(db, ctx, ctx.agent_key)
    if (
        config is not None
        and config.daily_cost_limit_usd is not None
        and daily_spend(db, ctx, clock.now().astimezone(UTC).date()) >= int(config.daily_cost_limit_usd * 1000000)
    ):
        raise AiError("ai.agent_limit_reached")


def _chain(db: Session, ctx: Context) -> ChainBudget | None:
    if ctx.correlation_id is None or ctx.agent_key is None:
        return None
    db.execute(
        insert(ChainBudget)
        .values(business_id=ctx.business_id, correlation_id=ctx.correlation_id)
        .on_conflict_do_nothing()
    )
    return db.scalar(
        select(ChainBudget)
        .where(ChainBudget.business_id == ctx.business_id, ChainBudget.correlation_id == ctx.correlation_id)
        .with_for_update()
    )


def reserve_model(db: Session, ctx: Context, cost: int, day: date | None = None) -> None:
    if ctx.agent_key is None:
        return
    day = day or clock.now().astimezone(UTC).date()
    require_enabled(db, ctx)
    row = settings(db, ctx, ctx.agent_key, lock=True)
    if (row is not None and not row.enabled) or (row is None and not AGENTS[ctx.agent_key].default_enabled):
        raise AiError("ai.agent_disabled")
    limit = int(row.daily_cost_limit_usd * 1000000) if row and row.daily_cost_limit_usd is not None else None
    legacy_cost = daily_spend(db, ctx, day)
    db.execute(
        insert(AgentUsageDaily)
        .values(business_id=ctx.business_id, agent_key=ctx.agent_key, day=day, cost_micros=legacy_cost)
        .on_conflict_do_nothing()
    )
    stmt = update(AgentUsageDaily).where(
        AgentUsageDaily.business_id == ctx.business_id,
        AgentUsageDaily.agent_key == ctx.agent_key,
        AgentUsageDaily.day == day,
    )
    if limit is not None:
        stmt = stmt.where(AgentUsageDaily.cost_micros + cost <= limit, AgentUsageDaily.cost_micros < limit)
    if db.scalar(stmt.values(cost_micros=AgentUsageDaily.cost_micros + cost).returning(AgentUsageDaily.id)) is None:
        raise AiError("ai.agent_limit_reached")
    chain = _chain(db, ctx)
    if chain is not None:
        if chain.stopped_reason:
            raise AiError(chain.stopped_reason)
        if chain.model_calls >= MAX_CALLS:
            raise AiError("ai.chain_call_limit")
        if chain.cost_micros + cost > MAX_COST_MICROS:
            raise AiError("ai.chain_cost_limit")
        chain.model_calls += 1
        chain.cost_micros += cost
        db.flush()


def reconcile_model(db: Session, ctx: Context, day: date, delta_cost: int) -> None:
    if ctx.agent_key is None:
        return
    db.execute(
        update(AgentUsageDaily)
        .where(
            AgentUsageDaily.business_id == ctx.business_id,
            AgentUsageDaily.agent_key == ctx.agent_key,
            AgentUsageDaily.day == day,
        )
        .values(cost_micros=AgentUsageDaily.cost_micros + delta_cost)
    )
    chain = _chain(db, ctx)
    if chain is not None:
        chain.cost_micros += delta_cost
        if chain.cost_micros > MAX_COST_MICROS and chain.stopped_reason is None:
            # Reported usage can exceed a provider's declared bound; already incurred cost cannot be undone.
            chain.stopped_reason = "ai.chain_cost_limit"
        db.flush()


def require_active_chain(db: Session, ctx: Context) -> None:
    reason = db.scalar(
        select(ChainBudget.stopped_reason).where(
            ChainBudget.business_id == ctx.business_id, ChainBudget.correlation_id == ctx.correlation_id
        )
    )
    if reason:
        raise AiError(reason)


def reserve_action(db: Session, ctx: Context) -> None:
    chain = _chain(db, ctx)
    if chain is not None:
        if chain.stopped_reason:
            raise AiError(chain.stopped_reason)
        if chain.actions >= MAX_ACTIONS:
            raise AiError("ai.chain_action_limit")
        chain.actions += 1
        db.flush()


def stop_chain(db: Session, ctx: Context, reason: str) -> None:
    chain = _chain(db, ctx)
    if chain is not None and chain.stopped_reason is None:
        chain.stopped_reason = reason
        db.flush()


def notify_limit(ctx: Context, *, force: bool = False, day: date | None = None) -> None:
    """Own committed transaction after a rejected reservation; one in-app notice per agent per UTC day."""
    if ctx.agent_key is None:
        return
    day = day or clock.now().astimezone(UTC).date()
    with transaction(ctx) as db:
        config = settings(db, ctx, ctx.agent_key)
        if config is None or config.daily_cost_limit_usd is None:
            return
        db.execute(
            insert(AgentUsageDaily)
            .values(
                business_id=ctx.business_id, agent_key=ctx.agent_key, day=day, cost_micros=daily_spend(db, ctx, day)
            )
            .on_conflict_do_nothing()
        )
        stmt = update(AgentUsageDaily).where(
            AgentUsageDaily.business_id == ctx.business_id,
            AgentUsageDaily.agent_key == ctx.agent_key,
            AgentUsageDaily.day == day,
            AgentUsageDaily.notified_at.is_(None),
        )
        if not force:
            stmt = stmt.where(AgentUsageDaily.cost_micros >= int(config.daily_cost_limit_usd * 1000000))
        if db.scalar(stmt.values(notified_at=clock.now()).returning(AgentUsageDaily.id)) is None:
            return
        notifications.notify_users(
            db,
            ctx,
            [u.id for u in identity.list_users_with_permission(db, ctx, "org.settings.write")],
            kind="ai.agent_limit_reached",
            title=f"{AGENTS[ctx.agent_key].name} daily cost limit blocked AI work",
            body="An AI attempt did not fit this agent's daily allowance, including in-flight reservations. "
            "The allowance resets at UTC midnight; an owner can raise the limit.",
        )
