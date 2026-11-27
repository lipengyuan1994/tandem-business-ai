"""payments: card refunds (sent after approval, or found in Stripe's Dashboard) and disputes

Revision ID: 20261127_2200_payments_refunds_disputes
Revises: 20261127_1800_payments_pay_links
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261127_2200_payments_refunds_disputes"
down_revision = "20261127_1800_payments_pay_links"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
LIVE = "'proposed','sending','pending','succeeded'"


def upgrade() -> None:
    op.create_table(
        "payment_refunds",
        *scoped_columns(),
        sa.Column("request_id", U, nullable=False),
        sa.Column("invoice_id", U, nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("invoice_refund_id", U),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("proposal_id", U),
        sa.Column("stripe_refund_id", sa.String(80)),
        sa.Column("failure_reason", sa.String(80)),
        sa.Column("requested_by", U),
        sa.Column("sent_at", TS),
        sa.Column("settled_at", TS),
        sa.Column("last_error", sa.String(500)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("stripe_refund_id"),
        composite_fk("request_id", "payment_requests"),
        sa.CheckConstraint("source in ('credit_note','surplus','dashboard')", name="source"),
        sa.CheckConstraint("status in ('proposed','sending','pending','succeeded','failed','canceled')", name="status"),
        sa.CheckConstraint("(source = 'credit_note') = (invoice_refund_id is not null)", name="credit_note"),
        sa.CheckConstraint("amount_minor > 0", name="amount"),
    )
    op.create_index("ix_payment_refunds_invoice", "payment_refunds", ["business_id", "invoice_id"])
    op.create_index(
        "uq_payment_refunds_credit",
        "payment_refunds",
        ["business_id", "invoice_refund_id"],
        unique=True,
        postgresql_where=sa.text(f"invoice_refund_id is not null and status in ({LIVE})"),
    )
    scoped_table("payment_refunds")
    op.create_table(
        "payment_disputes",
        *scoped_columns(),
        sa.Column("request_id", U, nullable=False),
        sa.Column("invoice_id", U, nullable=False),
        sa.Column("stripe_dispute_id", sa.String(80), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("stripe_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(40)),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("due_by", TS),
        sa.Column("opened_at", TS, nullable=False),
        sa.Column("closed_at", TS),
        sa.Column("reversed_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("stripe_dispute_id"),
        composite_fk("request_id", "payment_requests"),
        sa.CheckConstraint("outcome in ('open','won','lost')", name="outcome"),
        sa.CheckConstraint("amount_minor > 0 and reversed_minor >= 0", name="amount"),
    )
    op.create_index("ix_payment_disputes_invoice", "payment_disputes", ["business_id", "invoice_id"])
    scoped_table("payment_disputes")


def downgrade() -> None:
    disable_business_rls("payment_disputes")
    op.drop_index("ix_payment_disputes_invoice", table_name="payment_disputes")
    op.drop_table("payment_disputes")
    disable_business_rls("payment_refunds")
    op.drop_index("uq_payment_refunds_credit", table_name="payment_refunds")
    op.drop_index("ix_payment_refunds_invoice", table_name="payment_refunds")
    op.drop_table("payment_refunds")
