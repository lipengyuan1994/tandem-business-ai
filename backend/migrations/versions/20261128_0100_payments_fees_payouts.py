"""payments: Stripe fees on paid payments, and payouts to the business's bank (M21 phase 4)

Revision ID: 20261128_0100_payments_fees_payouts
Revises: 20261127_2200_payments_refunds_disputes
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import disable_business_rls, scoped_columns, scoped_table

revision = "20261128_0100_payments_fees_payouts"
down_revision = "20261127_2200_payments_refunds_disputes"
branch_labels = None
depends_on = None
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("payment_requests", sa.Column("stripe_fee_minor", sa.BigInteger))
    op.add_column("payment_requests", sa.Column("net_minor", sa.BigInteger))
    op.create_table(
        "payment_payouts",
        *scoped_columns(),
        sa.Column("stripe_payout_id", sa.String(80), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("arrival_date", TS),
        sa.Column("automatic", sa.Boolean, nullable=False),
        sa.Column("method", sa.String(16)),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("charges_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("refunds_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("disputes_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("other_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("fees_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("transaction_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summarized_at", TS),
        sa.Column("summary_complete", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_error", sa.String(500)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("stripe_payout_id"),
        sa.CheckConstraint("status in ('pending','in_transit','paid','failed','canceled')", name="status"),
    )
    op.create_index("ix_payment_payouts_arrival", "payment_payouts", ["business_id", "arrival_date"])
    scoped_table("payment_payouts")


def downgrade() -> None:
    disable_business_rls("payment_payouts")
    op.drop_index("ix_payment_payouts_arrival", table_name="payment_payouts")
    op.drop_table("payment_payouts")
    op.drop_column("payment_requests", "net_minor")
    op.drop_column("payment_requests", "stripe_fee_minor")
