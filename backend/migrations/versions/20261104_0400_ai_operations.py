"""Durable staff operation claims, turn serialization and retry identity (batch C)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261104_0400_ai_operations"
down_revision = "20261103_1600_ai_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_operations", *scoped_columns(),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=False),
        sa.Column("operation_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True)),
        sa.Column("proposal_id", UUID(as_uuid=True)),
        sa.Column("message_id", UUID(as_uuid=True)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "user_id", "kind", "resource_id", "operation_key",
                            name="uq_ai_operations_key"),
        composite_fk("run_id", "ai_runs"),
        composite_fk("message_id", "assistant_messages"),
        sa.CheckConstraint("status in ('active','idle','succeeded','failed')", name="status"),
    )
    op.create_index("uq_ai_operations_active_thread", "ai_operations", ["business_id", "resource_id"],
                    unique=True, postgresql_where=sa.text("kind='copilot' and status='active'"))
    scoped_table("ai_operations")


def downgrade() -> None:
    disable_business_rls("ai_operations")
    op.drop_table("ai_operations")
