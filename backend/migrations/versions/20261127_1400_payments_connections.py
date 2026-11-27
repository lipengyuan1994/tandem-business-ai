"""payments: Stripe connection per business (through the payments relay) and forwarded Stripe events

Revision ID: 20261127_1400_payments_connections
Revises: 20261125_1700_bill_credits
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from migrations.helpers import disable_business_rls, scoped_columns, scoped_table

revision = "20261127_1400_payments_connections"
down_revision = "20261125_1700_bill_credits"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "payment_connections",
        *scoped_columns(),
        sa.Column("provider", sa.String(12), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("relay_url", sa.String(500), nullable=False),
        sa.Column("relay_client_id", sa.String(64)),
        sa.Column("relay_secret_sealed", sa.Text),
        sa.Column("stripe_account_id", sa.String(64)),
        sa.Column("country", sa.String(2)),
        sa.Column("default_currency", sa.String(3)),
        sa.Column("charges_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("payouts_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("details_submitted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("requirements_due", JSONB, nullable=False, server_default="[]"),
        sa.Column("disabled_reason", sa.String(100)),
        sa.Column("connected_by", U),
        sa.Column("connected_at", TS),
        sa.Column("last_checked_at", TS),
        sa.Column("last_error", sa.String(500)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id"),
        sa.CheckConstraint("provider in ('stripe')", name="provider"),
        sa.CheckConstraint("mode in ('test','live')", name="mode"),
        sa.CheckConstraint("status in ('onboarding','active','restricted','disconnected')", name="status"),
    )
    scoped_table("payment_connections")
    op.create_table(
        "payment_events",
        *scoped_columns(),
        sa.Column("stripe_event_id", sa.String(80), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(40)),
        sa.Column("object_id", sa.String(80)),
        sa.Column("livemode", sa.Boolean, nullable=False),
        sa.Column("stripe_created_at", TS),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("processed_at", TS),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "stripe_event_id"),
        sa.CheckConstraint("outcome in ('pending','applied','ignored','failed')", name="outcome"),
    )
    op.create_index("ix_payment_events_outcome", "payment_events", ["business_id", "outcome"])
    scoped_table("payment_events")


def downgrade() -> None:
    disable_business_rls("payment_events")
    op.drop_index("ix_payment_events_outcome", table_name="payment_events")
    op.drop_table("payment_events")
    disable_business_rls("payment_connections")
    op.drop_table("payment_connections")
