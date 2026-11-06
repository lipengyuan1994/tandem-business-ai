"""ai: export of every AI table, and erasure of an erased person's traces in AI texts (M18, contracts §2).

What holds text (§2): `ai_runs.result` and `assistant_messages.content` (plus the staff-written thread title).
Run steps hold no customer text (ids, tool names, counts, error codes) and effects hold ids only; both are append-only.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, case, false, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .customer_care.models import CareCase, CareDelivery
from .models import AiEffect, AiRun, AiRunStep, AiUsageDaily, AssistantMessage, AssistantThread
from .team_models import AgentSettings, AgentUsageDaily, ChainBudget

TABLES = [
    CareCase,
    CareDelivery,
    AiRun,
    AiRunStep,
    AiEffect,
    AiUsageDaily,
    AssistantThread,
    AssistantMessage,
    AgentSettings,
    AgentUsageDaily,
    ChainBudget,
]
ERASED = "[erased]"


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(db, ctx, TABLES)
    return export_tables(tables, streaming=streaming)


def _linked_run(target: ErasureTarget) -> ColumnElement[bool]:
    """Runs started by one of the person's records (trigger key `intake:<message_id>`) or that created one of the
    person's proposals or holds lose their whole result text."""
    ids = target.all_ids
    if not ids:
        return false()
    by_trigger = or_(*(AiRun.trigger_key.contains(str(i), autoescape=True) for i in ids))
    by_effect = AiRun.id.in_(select(AiEffect.run_id).where(AiEffect.ref_id.in_(ids)))
    return or_(by_trigger, by_effect)


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    needles = target.needles
    linked = _linked_run(target)
    new_result = case((linked, func.scrub_jsonb(AiRun.result)), else_=func.redact_jsonb(AiRun.result, needles))
    runs = db.execute(
        update(AiRun)
        .where(AiRun.business_id == ctx.business_id, AiRun.result.is_distinct_from(new_result))
        .values(result=new_result, version=AiRun.version + 1)
        .execution_options(synchronize_session=False)
    )
    linked_runs = select(AiRun.id).where(AiRun.business_id == ctx.business_id, linked)
    ids = literal(target.all_ids, ARRAY(UUID(as_uuid=True)))
    message_linked = or_(AssistantMessage.proposal_ids.overlap(ids), AssistantMessage.run_id.in_(linked_runs))
    new_content = case((message_linked, literal(ERASED)), else_=func.redact_text(AssistantMessage.content, needles))
    messages = db.execute(
        update(AssistantMessage)
        .where(AssistantMessage.business_id == ctx.business_id, AssistantMessage.content.is_distinct_from(new_content))
        .values(content=new_content, version=AssistantMessage.version + 1)
        .execution_options(synchronize_session=False)
    )
    new_title = func.left(func.redact_text(AssistantThread.title, needles), 120)
    threads = db.execute(
        update(AssistantThread)
        .where(AssistantThread.business_id == ctx.business_id, AssistantThread.title.is_distinct_from(new_title))
        .values(title=new_title, version=AssistantThread.version + 1)
        .execution_options(synchronize_session=False)
    )
    return {
        "ai_runs_redacted": rows_affected(runs),
        "assistant_messages_redacted": rows_affected(messages),
        "assistant_threads_redacted": rows_affected(threads),
    }
