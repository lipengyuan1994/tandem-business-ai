"""Tables of the ai module (docs/roadmap/ai/contracts.md §4). All business-scoped with forced row-level security.

Storage rule (§2): prompt and raw completion text are never stored. Only `ai_runs.result`, the redacted step fields
(ids, tool names, counts, error codes) and `assistant_messages.content` hold text; erasure scrubs them (lifecycle.py).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, ForeignKeyConstraint, Index, Integer, String, Text
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args

from .customer_care.models import CareCase as CareCase
from .customer_care.models import CareDelivery as CareDelivery

USE_CASES = (
    "intake", "copilot", "summary", "reorder_note", "briefing", "procurement", "campaign_draft", "payment_followup",
    "safety", "grounding", "dictation", "customer_care", "knowledge_index", "knowledge_query",
)  # fmt: skip
TRIGGER_TYPES = ("event", "job", "api", "cron")
RUN_STATUSES = ("running", "succeeded", "failed", "step_limit", "budget_exceeded", "policy_blocked")
MODEL_CLASSES = ("agent", "fast_extract", "writer", "safety", "grounding", "embed", "stt", "tts")
STEP_KINDS = ("tool_call", "final", "repair", "completion")
EFFECT_KINDS = ("hold", "proposal")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


def _fk(col: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["business_id", col], [f"{table}.business_id", f"{table}.id"])


class AiRun(BusinessScopedBase):
    __tablename__ = "ai_runs"
    use_case: Mapped[str] = mapped_column(String(32))
    trigger_type: Mapped[str] = mapped_column(String(8))
    trigger_key: Mapped[str] = mapped_column(Text)
    on_behalf_of_user_id: Mapped[uuid.UUID | None]
    prompt_name: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[int] = mapped_column(Integer)
    model_class: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="running")
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql("false"))
    steps: Mapped[int] = mapped_column(Integer, default=0, server_default=sql("0"))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=sql("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=sql("0"))
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default=sql("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=sql("0"))
    error_code: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql("'{}'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(server_default=sql("now()"))
    finished_at: Mapped[datetime | None]
    correlation_id: Mapped[uuid.UUID | None]
    causation_id: Mapped[uuid.UUID | None]
    agent_key: Mapped[str | None] = mapped_column(String(32))
    chain_depth: Mapped[int | None]
    __table_args__ = scoped_table_args(
        CheckConstraint(_in("use_case", USE_CASES), name="use_case"),
        CheckConstraint(_in("trigger_type", TRIGGER_TYPES), name="trigger_type"),
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        CheckConstraint(_in("model_class", MODEL_CLASSES), name="model_class"),
        Index("ix_ai_runs_started", "business_id", sql("started_at desc")),
        Index("ix_ai_runs_trigger", "business_id", "trigger_key"),
        Index("ix_ai_runs_chain", "business_id", "correlation_id"),
    )


class AiRunStep(BusinessScopedBase):
    """Append-only: one row per model step; never prompt or completion text (§2)."""

    __tablename__ = "ai_run_steps"
    run_id: Mapped[uuid.UUID]
    step_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12))
    tool: Mapped[str | None] = mapped_column(Text)
    arguments_redacted: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql("'{}'::jsonb"))
    result_summary: Mapped[str] = mapped_column(String(500), default="", server_default="")
    error_code: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=sql("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default=sql("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=sql("0"))
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    __table_args__ = scoped_table_args(
        _fk("run_id", "ai_runs"),
        Index("uq_ai_run_steps_step", "run_id", "step_no", unique=True),
        CheckConstraint(_in("kind", STEP_KINDS), name="kind"),
    )


class AiEffect(BusinessScopedBase):
    """Append-only: a hold or proposal a tool created, written in the same transaction as it (§4, §10)."""

    __tablename__ = "ai_effects"
    trigger_key: Mapped[str] = mapped_column(Text)
    effect_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))
    ref_id: Mapped[uuid.UUID]
    run_id: Mapped[uuid.UUID]
    __table_args__ = scoped_table_args(
        _fk("run_id", "ai_runs"),
        Index("uq_ai_effects_key", "business_id", "effect_key", unique=True),
        Index("ix_ai_effects_trigger", "business_id", "trigger_key"),
        CheckConstraint(_in("kind", EFFECT_KINDS), name="kind"),
    )


class AiUsageDaily(BusinessScopedBase):
    __tablename__ = "ai_usage_daily"
    day: Mapped[date] = mapped_column(Date)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=sql("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=sql("0"))
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default=sql("0"))
    runs: Mapped[int] = mapped_column(Integer, default=0, server_default=sql("0"))
    notified_80_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(Index("uq_ai_usage_daily_day", "business_id", "day", unique=True))


class AssistantThread(BusinessScopedBase):
    __tablename__ = "assistant_threads"
    user_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(String(120))
    __table_args__ = scoped_table_args(Index("ix_assistant_threads_user", "business_id", "user_id"))


class AssistantMessage(BusinessScopedBase):
    __tablename__ = "assistant_messages"
    thread_id: Mapped[uuid.UUID]
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None]
    proposal_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, server_default=sql("'{}'::uuid[]")
    )
    __table_args__ = scoped_table_args(
        _fk("thread_id", "assistant_threads"),
        _fk("run_id", "ai_runs"),
        Index("ix_assistant_messages_thread", "thread_id", "created_at"),
        CheckConstraint("role in ('user','assistant')", name="role"),
        CheckConstraint("char_length(content) <= 8000", name="content_length"),
    )
