"""Server verification metadata for extraction operation retries; no source/model text."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261104_0500_ai_op_details"
down_revision = "20261104_0400_ai_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_operations", sa.Column("details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("ai_operations", "details")
