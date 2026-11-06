"""P10-B1 team configuration, causality and atomic accounting; build on voice."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import scoped_columns, scoped_table

revision = "20261106_1000_ai_team"
down_revision = "20261106_0800_voice_dictation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("ai_runs", "action_proposals", "domain_events", "jobs"):
        op.add_column(table, sa.Column("correlation_id", UUID(as_uuid=True)))
        op.add_column(table, sa.Column("causation_id", UUID(as_uuid=True)))
        op.add_column(table, sa.Column("agent_key", sa.String(32)))
        op.add_column(table, sa.Column("chain_depth", sa.Integer))
        op.create_index(f"ix_{table}_chain", table, ["business_id", "correlation_id"])
    op.create_table("ai_agent_settings", *scoped_columns(),
        sa.Column("agent_key", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("daily_cost_limit_usd", sa.Numeric(18, 6)),
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("daily_cost_limit_usd is null or daily_cost_limit_usd >= 0", name="limit"))
    op.create_index("uq_ai_agent_settings_key", "ai_agent_settings", ["business_id", "agent_key"], unique=True)
    op.create_table("ai_agent_usage_daily", *scoped_columns(),
        sa.Column("agent_key", sa.String(32), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("cost_micros", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("business_id", "id"))
    op.create_index("uq_ai_agent_usage_day", "ai_agent_usage_daily", ["business_id", "agent_key", "day"], unique=True)
    op.create_table("ai_chain_budgets", *scoped_columns(),
        sa.Column("correlation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("model_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("actions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_micros", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("stopped_reason", sa.String(100)),
        sa.UniqueConstraint("business_id", "id"))
    op.create_index("uq_ai_chain_budget", "ai_chain_budgets", ["business_id", "correlation_id"], unique=True)
    for table in ("ai_agent_settings", "ai_agent_usage_daily", "ai_chain_budgets"):
        op.execute(f"grant select, insert, update, delete on {table} to smb_app")
        scoped_table(table)


def downgrade() -> None:
    for table in ("ai_chain_budgets", "ai_agent_usage_daily", "ai_agent_settings"):
        op.drop_table(table)
    for table in ("ai_runs", "action_proposals", "domain_events", "jobs"):
        op.drop_index(f"ix_{table}_chain", table_name=table)
        for col in ("chain_depth", "agent_key", "causation_id", "correlation_id"):
            op.drop_column(table, col)
