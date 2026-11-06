"""Versioned settings; advisory locks also cover a default with no persisted row."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.platform import audit
from app.platform.context import Context
from app.platform.errors import Conflict

from .agents import get_agent
from .team_models import AgentSettings
from .team_schemas import AgentPatch


def settings(db: Session, ctx: Context, key: str, *, lock: bool = False, write: bool = False) -> AgentSettings | None:
    get_agent(key)
    if lock:
        function = "pg_advisory_xact_lock" if write else "pg_advisory_xact_lock_shared"
        db.execute(text(f"select {function}(hashtextextended(:key, 0))"), {"key": f"ai-agent:{ctx.business_id}:{key}"})
    return db.scalar(
        select(AgentSettings)
        .where(AgentSettings.business_id == ctx.business_id, AgentSettings.agent_key == key)
        .execution_options(populate_existing=True)
    )


def enabled(db: Session, ctx: Context, key: str, *, lock: bool = False) -> bool:
    row = settings(db, ctx, key, lock=lock)
    return row.enabled if row is not None else get_agent(key).default_enabled


def patch_settings(db: Session, ctx: Context, key: str, data: AgentPatch) -> AgentSettings:
    ctx.require("org.settings.write")
    row = settings(db, ctx, key, lock=True, write=True)
    version = row.version if row is not None else 0
    if version != data.version:
        raise Conflict("ai.agent_version_conflict", current_version=version)
    before = {"enabled": row.enabled, "daily_cost_limit_usd": str(row.daily_cost_limit_usd)} if row else {}
    if row is None:
        row = AgentSettings(business_id=ctx.business_id, agent_key=key, enabled=get_agent(key).default_enabled)
        db.add(row)
    else:
        row.version += 1
    for name in data.model_fields_set - {"version"}:
        setattr(row, name, getattr(data, name))
    row.updated_by = ctx.user_id
    db.flush()
    audit.record(
        db,
        ctx,
        action="ai.agent.settings_changed",
        target_type="ai_agent",
        target_id=row.id,
        summary=f"AI agent {key} settings v{row.version}",
        changes={
            "before": before,
            "after": {"enabled": row.enabled, "daily_cost_limit_usd": str(row.daily_cost_limit_usd), "agent_key": key},
        },
    )
    return row
