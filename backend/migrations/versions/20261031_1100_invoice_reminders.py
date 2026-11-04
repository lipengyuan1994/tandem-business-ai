"""Track manually approved invoice reminders.

Revision ID: 20261031_1100_invoice_reminders
Revises: 20261031_1000_platform_prerequisites
"""

import sqlalchemy as sa
from alembic import op

revision = "20261031_1100_invoice_reminders"
down_revision = "20261031_1000_platform_prerequisites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "last_reminder_at")
