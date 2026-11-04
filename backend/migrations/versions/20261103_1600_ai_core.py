"""ai: runs, run steps (append-only), effects (append-only), daily usage, assistant threads and messages

Revision ID: 20261103_1600_ai_core
Revises: 20261031_1200_purchasing_quotes
Created (UTC): 2026-11-03

docs/roadmap/ai/contracts.md §4. Every table is business-scoped with forced row-level security.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261103_1600_ai_core"
down_revision = "20261031_1200_purchasing_quotes"
branch_labels = None
depends_on = None

TABLES = ("ai_runs", "ai_run_steps", "ai_effects", "ai_usage_daily", "assistant_threads", "assistant_messages")
APPEND_ONLY = ("ai_run_steps", "ai_effects")
U = UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
ZERO = sa.text("0")
EMPTY_JSON = sa.text("'{}'::jsonb")

USE_CASES = (
    "'intake','copilot','summary','reorder_note','briefing','procurement','campaign_draft','payment_followup','safety'"
)
RUN_STATUSES = "'running','succeeded','failed','step_limit','budget_exceeded','policy_blocked'"
MODEL_CLASSES = "'agent','fast_extract','writer','safety','embed','stt','tts'"


def upgrade() -> None:
    op.create_table(
        "ai_runs", *scoped_columns(),
        sa.Column("use_case", sa.String(32), nullable=False),
        sa.Column("trigger_type", sa.String(8), nullable=False),
        sa.Column("trigger_key", sa.Text, nullable=False),
        sa.Column("on_behalf_of_user_id", U),
        sa.Column("prompt_name", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.Integer, nullable=False),
        sa.Column("model_class", sa.String(16), nullable=False),
        sa.Column("provider", sa.Text), sa.Column("model", sa.Text),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("needs_human", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("steps", sa.Integer, nullable=False, server_default=ZERO),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default=ZERO),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default=ZERO),
        sa.Column("cost_micros", sa.BigInteger, nullable=False, server_default=ZERO),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default=ZERO),
        sa.Column("error_code", sa.Text),
        sa.Column("result", JSONB, nullable=False, server_default=EMPTY_JSON),
        sa.Column("started_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TS),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint(f"use_case in ({USE_CASES})", name="use_case"),
        sa.CheckConstraint("trigger_type in ('event','job','api','cron')", name="trigger_type"),
        sa.CheckConstraint(f"status in ({RUN_STATUSES})", name="status"),
        sa.CheckConstraint(f"model_class in ({MODEL_CLASSES})", name="model_class"),
    )  # fmt: skip
    op.create_index("ix_ai_runs_started", "ai_runs", ["business_id", sa.text("started_at desc")])
    op.create_index("ix_ai_runs_trigger", "ai_runs", ["business_id", "trigger_key"])
    op.create_table(
        "ai_run_steps", *scoped_columns(),
        sa.Column("run_id", U, nullable=False),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("tool", sa.Text),
        sa.Column("arguments_redacted", JSONB, nullable=False, server_default=EMPTY_JSON),
        sa.Column("result_summary", sa.String(500), nullable=False, server_default=""),
        sa.Column("error_code", sa.Text),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default=ZERO),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default=ZERO),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default=ZERO),
        sa.Column("provider", sa.Text), sa.Column("model", sa.Text),
        sa.UniqueConstraint("business_id", "id"), composite_fk("run_id", "ai_runs"),
        sa.CheckConstraint("kind in ('tool_call','final','repair','completion')", name="kind"),
    )  # fmt: skip
    op.create_index("uq_ai_run_steps_step", "ai_run_steps", ["run_id", "step_no"], unique=True)
    op.create_table(
        "ai_effects", *scoped_columns(),
        sa.Column("trigger_key", sa.Text, nullable=False),
        sa.Column("effect_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("ref_id", U, nullable=False),
        sa.Column("run_id", U, nullable=False),
        sa.UniqueConstraint("business_id", "id"), composite_fk("run_id", "ai_runs"),
        sa.CheckConstraint("kind in ('hold','proposal')", name="kind"),
    )  # fmt: skip
    op.create_index("uq_ai_effects_key", "ai_effects", ["business_id", "effect_key"], unique=True)
    op.create_index("ix_ai_effects_trigger", "ai_effects", ["business_id", "trigger_key"])
    op.create_table(
        "ai_usage_daily", *scoped_columns(),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("input_tokens", sa.BigInteger, nullable=False, server_default=ZERO),
        sa.Column("output_tokens", sa.BigInteger, nullable=False, server_default=ZERO),
        sa.Column("cost_micros", sa.BigInteger, nullable=False, server_default=ZERO),
        sa.Column("runs", sa.Integer, nullable=False, server_default=ZERO),
        sa.Column("notified_80_at", TS),
        sa.UniqueConstraint("business_id", "id"),
    )  # fmt: skip
    op.create_index("uq_ai_usage_daily_day", "ai_usage_daily", ["business_id", "day"], unique=True)
    op.create_table(
        "assistant_threads", *scoped_columns(),
        sa.Column("user_id", U, nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.UniqueConstraint("business_id", "id"),
    )  # fmt: skip
    op.create_index("ix_assistant_threads_user", "assistant_threads", ["business_id", "user_id"])
    op.create_table(
        "assistant_messages", *scoped_columns(),
        sa.Column("thread_id", U, nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("run_id", U),
        sa.Column("proposal_ids", ARRAY(U), nullable=False, server_default=sa.text("'{}'::uuid[]")),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("thread_id", "assistant_threads"), composite_fk("run_id", "ai_runs"),
        sa.CheckConstraint("role in ('user','assistant')", name="role"),
        sa.CheckConstraint("char_length(content) <= 8000", name="content_length"),
    )  # fmt: skip
    op.create_index("ix_assistant_messages_thread", "assistant_messages", ["thread_id", "created_at"])
    for table in TABLES:
        scoped_table(table, append=table in APPEND_ONLY)


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
