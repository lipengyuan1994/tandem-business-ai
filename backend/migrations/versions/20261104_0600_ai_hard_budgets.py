"""Atomic AI reservations, installation ceilings and public allowances (ops batch G)."""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import scoped_columns, scoped_table

revision = "20261104_0600_ai_budgets"
down_revision = "20261104_0500_ai_op_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_cost_periods",
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("period", sa.Date, primary_key=True),
        sa.Column("cost_micros", sa.BigInteger, nullable=False, server_default="0"),
        sa.CheckConstraint("kind in ('day','month')", name="kind"),
        sa.CheckConstraint("cost_micros >= 0", name="cost_nonnegative"),
    )
    # Preserve already-recorded spending when upgrading mid-period.
    op.execute("""insert into ai_cost_periods (kind, period, cost_micros)
        select 'day', day, sum(cost_micros) from ai_usage_daily group by day""")
    op.execute("""insert into ai_cost_periods (kind, period, cost_micros)
        select 'month', date_trunc('month', day)::date, sum(cost_micros)
        from ai_usage_daily group by date_trunc('month', day)::date""")
    op.create_table(
        "ai_public_allowances", *scoped_columns(),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("period", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.BigInteger, nullable=False, server_default="0"),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("used >= 0", name="used_nonnegative"),
        sa.CheckConstraint("kind in ('hour','runs','tokens')", name="kind"),
    )
    op.create_index("uq_ai_public_allowance", "ai_public_allowances", ["business_id", "kind", "period"], unique=True)
    scoped_table("ai_public_allowances")
    op.create_table(
        "ai_budget_notices", *scoped_columns(),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("threshold", sa.Integer, nullable=False),
        sa.UniqueConstraint("business_id", "id"),
    )
    op.create_index(
        "uq_ai_budget_notice", "ai_budget_notices", ["business_id", "scope", "period", "threshold"], unique=True
    )
    scoped_table("ai_budget_notices")
    op.create_index(
        "ix_jobs_ai_intake_pending", "jobs", ["business_id"],
        postgresql_where=sa.text("kind = 'ai.intake' and status in ('queued','running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_ai_intake_pending", "jobs")
    op.drop_table("ai_budget_notices")
    op.drop_table("ai_public_allowances")
    op.drop_table("ai_cost_periods")
