"""Immutable run context, fixed transition checks and adapters for approvals' registry hooks."""

from dataclasses import replace
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.context import Context

from .agents import ACTION_OWNERS, AGENTS, USE_CASE_OWNERS
from .errors import AiError
from .models import AiRun
from .team_settings import enabled

MAX_DEPTH = 6


def subject_context(db: Session, ctx: Context, field: str, ident: UUID, *, period: str = "") -> Context:
    """A scan discovers independent subjects; waiting procurement retains its original workflow budget."""
    from app.modules.approvals import public as approvals

    source = approvals.workflow_for_result(db, ctx, field, ident) if field != "invoice_id" else None
    if source is not None:
        return replace(ctx, **source)
    return replace(
        ctx,
        correlation_id=uuid5(NAMESPACE_URL, f"ai-chain:{ctx.business_id}:{field}:{ident}:{period}"),
        agent_key=None,
        chain_depth=0,
    )


def run_context(ctx: Context, use_case: str) -> Context:
    key = ctx.agent_key or USE_CASE_OWNERS.get(use_case)
    if key is None and use_case in {"safety", "grounding", "reply_guardrail"}:
        key = "front_desk"
    if use_case == "dictation":
        key = None
    return replace(
        ctx,
        agent_key=key,
        correlation_id=ctx.correlation_id or ctx.ai_run_id,
        chain_depth=ctx.chain_depth or 1,
    )


def admission_reason(db: Session, ctx: Context) -> str | None:
    if ctx.agent_key is None:
        return None
    require_enabled(db, ctx)
    from .team_budget import check_daily_limit
    from .team_models import ChainBudget

    check_daily_limit(db, ctx)

    stopped = db.scalar(
        select(ChainBudget.stopped_reason).where(
            ChainBudget.business_id == ctx.business_id, ChainBudget.correlation_id == ctx.correlation_id
        )
    )
    if stopped:
        return stopped
    if ctx.chain_depth > MAX_DEPTH:
        return "ai.chain_depth_exceeded"
    if ctx.causing_agent_key is not None and (
        ctx.causing_agent_key not in AGENTS or ctx.agent_key not in AGENTS[ctx.causing_agent_key].handoffs
    ):
        return "ai.chain_transition_blocked"
    return None


def require_enabled(db: Session, ctx: Context) -> None:
    keys = (["operations_manager"] if ctx.ai_manager_required else []) + ([ctx.agent_key] if ctx.agent_key else [])
    for key in dict.fromkeys(keys):
        if not enabled(db, ctx, key, lock=True):
            raise AiError("ai.agent_disabled", f"{AGENTS[key].name} is off. Ask an owner to turn it on.")


def effect_context(db: Session, ctx: Context, action_type: str = "") -> Context:
    run = db.scalar(select(AiRun).where(AiRun.business_id == ctx.business_id, AiRun.id == ctx.ai_run_id))
    if run is not None:
        return replace(
            ctx,
            agent_key=run.agent_key or USE_CASE_OWNERS.get(run.use_case),
            correlation_id=run.correlation_id or ctx.correlation_id or run.id,
            causation_id=run.id,
            chain_depth=run.chain_depth or 1,
            ai_manager_required=run.use_case == "copilot",
        )
    return replace(
        ctx,
        agent_key=ctx.agent_key or ACTION_OWNERS.get(action_type),
        correlation_id=ctx.correlation_id or ctx.ai_run_id,
    )


def proposal_context(db: Session, ctx: Context, action_type: str) -> Context:
    """Recover authoritative attribution for callers that reconstruct a context after a structured run."""
    if ctx.actor_kind != "assistant":
        return ctx
    ctx = effect_context(db, ctx, action_type)
    if ctx.agent_key is None:
        ctx = replace(ctx, agent_key=ACTION_OWNERS.get(action_type), correlation_id=ctx.correlation_id or ctx.ai_run_id)
    require_enabled(db, ctx)
    from .team_budget import require_active_chain

    require_active_chain(db, ctx)
    return ctx


def execution_context(db: Session, ctx: Context, action_type: str) -> Context:
    if ctx.agent_key is None and ctx.ai_run_id is not None:
        inferred = effect_context(db, ctx, action_type)
        return replace(ctx, agent_key=inferred.agent_key)
    return ctx


def policy_enabled(db: Session, ctx: Context) -> bool:
    if ctx.agent_key is None:
        from app.modules.approvals import public as approvals

        run = db.scalar(select(AiRun).where(AiRun.business_id == ctx.business_id, AiRun.id == ctx.ai_run_id))
        state = approvals.proposal_notification_state(db, ctx, ctx.proposal_id) if ctx.proposal_id else None
        key = (
            (run.agent_key or USE_CASE_OWNERS.get(run.use_case))
            if run
            else (ACTION_OWNERS.get(state["action_type"]) if state and state["origin_kind"] == "assistant" else None)
        )
        ctx = replace(ctx, agent_key=key)
    return ctx.agent_key is None or enabled(db, ctx, ctx.agent_key, lock=True)
