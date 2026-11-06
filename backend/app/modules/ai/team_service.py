"""Owner-facing projections; autonomy and execution remain owned by approvals."""

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.org import public as org
from app.platform import clock
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.models import DomainEvent

from .agents import AGENTS, get_agent
from .budget_models import CostPeriod
from .models import AiRun
from .public import status as ai_status
from .team_models import AgentUsageDaily
from .team_schemas import AgentOut, AgentsOut, TodayWork, WaitingItems
from .team_settings import settings


def agent_out(db: Session, ctx: Context, key: str) -> AgentOut:
    if not ctx.has("org.settings.write"):
        ctx.require("approvals.proposal.read")
    agent = get_agent(key)
    config = settings(db, ctx, key)
    is_enabled = config.enabled if config else agent.default_enabled
    today = clock.now().astimezone(UTC).date()
    since = datetime.combine(today, datetime.min.time(), UTC)
    owned = or_(AiRun.agent_key == key, and_(AiRun.agent_key.is_(None), AiRun.use_case.in_(agent.use_cases)))
    run_count, human_count = db.execute(
        select(func.count(), func.count().filter(AiRun.needs_human)).where(
            AiRun.business_id == ctx.business_id, owned, AiRun.started_at >= since
        )
    ).one()
    run_last = db.scalar(
        select(func.max(func.coalesce(AiRun.finished_at, AiRun.started_at))).where(
            AiRun.business_id == ctx.business_id, owned
        )
    )
    event_last = db.scalar(
        select(func.max(DomainEvent.occurred_at)).where(
            DomainEvent.business_id == ctx.business_id, DomainEvent.agent_key == key
        )
    )
    daily = db.scalar(
        select(AgentUsageDaily).where(
            AgentUsageDaily.business_id == ctx.business_id,
            AgentUsageDaily.agent_key == key,
            AgentUsageDaily.day == today,
        )
    )
    cost = (
        daily.cost_micros
        if daily
        else (
            db.scalar(
                select(func.sum(AiRun.cost_micros)).where(
                    AiRun.business_id == ctx.business_id, owned, AiRun.started_at >= since
                )
            )
            or 0
        )
    )
    summary = approvals.team_summary(db, ctx, key, agent.action_types, since)
    if key == "customer_care":
        from .customer_care.models import CareCase

        count, oldest = db.execute(
            select(func.count(), func.min(CareCase.created_at)).where(
                CareCase.business_id == ctx.business_id, CareCase.status == "open"
            )
        ).one()
        # A complaint's human resolution and its reply approval are separate Work items.
        summary["waiting"]["count"] += count
        summary["waiting"]["oldest_at"] = min((t for t in (oldest, summary["waiting"]["oldest_at"]) if t), default=None)
    availability = ai_status(db, ctx)
    limit = config.daily_cost_limit_usd if config else None
    installation = get_settings()
    installation_limited = any(
        (db.scalar(select(CostPeriod.cost_micros).where(CostPeriod.kind == kind, CostPeriod.period == period)) or 0)
        >= int(ceiling * 1000000)
        for kind, period, ceiling in (
            ("day", today, installation.ai_daily_cost_limit_usd),
            ("month", today.replace(day=1), installation.ai_monthly_cost_limit_usd),
        )
    )
    reason = None
    state = "on"
    if not is_enabled:
        state, reason = "off", "ai.agent_disabled"
    elif org.autonomy_paused(db, ctx):
        state, reason = "paused", "autonomy_paused"
    elif missing := [
        c
        for c in agent.model_classes
        if not availability.model_classes[c].enabled
        and not (availability.model_classes[c].reason or "").startswith("ai.off:")
    ]:
        state, reason = "unavailable", "; ".join(availability.model_classes[c].reason or c for c in missing)
    elif (
        (limit is not None and cost >= int(limit * 1000000))
        or installation_limited
        or availability.reason == "ai.budget_exceeded"
    ):
        state, reason = "limit_reached", "ai.agent_limit_reached" if limit is not None else "ai.budget_exceeded"
    return AgentOut(
        key=key,
        name=agent.name,
        job=agent.job,
        enabled=is_enabled,
        version=config.version if config else 0,
        updated_by=config.updated_by if config else None,
        autonomy=summary["autonomy"],
        today=TodayWork(
            runs=run_count,
            proposals_created=summary["proposals_created"],
            actions_executed_by_people=summary["actions_executed_by_people"],
            actions_executed_by_rules=summary["actions_executed_by_rules"],
            handed_to_person=human_count + summary["handed_to_person"],
        ),
        cost_today_micros=cost,
        daily_cost_limit_usd=limit,
        waiting=WaitingItems(**summary["waiting"]),
        last_activity_at=max(
            (t for t in (run_last, event_last, summary["last_activity_at"]) if t is not None), default=None
        ),
        status=state,
        reason=reason,
    )


def list_agents(db: Session, ctx: Context) -> AgentsOut:
    return AgentsOut(items=[agent_out(db, ctx, key) for key in AGENTS])
