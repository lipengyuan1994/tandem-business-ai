"""payments: invoice pay links and Stripe Checkout payment requests; online payments on the sales ledger

Revision ID: 20261127_1800_payments_pay_links
Revises: 20261127_1400_payments_connections
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261127_1800_payments_pay_links"
down_revision = "20261127_1400_payments_connections"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("invoice_payments", sa.Column("provider", sa.String(12)))
    op.add_column("invoice_payments", sa.Column("external_id", sa.String(80)))
    op.create_check_constraint("external", "invoice_payments", "(provider is null) = (external_id is null)")
    op.create_index(
        "uq_invoice_payments_external",
        "invoice_payments",
        ["business_id", "provider", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id is not null"),
    )
    op.create_table(
        "payment_links",
        *scoped_columns(),
        sa.Column("invoice_id", U, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by", U),
        sa.Column("revoked_at", TS),
        sa.Column("last_opened_at", TS),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "uq_payment_links_active",
        "payment_links",
        ["business_id", "invoice_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at is null"),
    )
    scoped_table("payment_links")
    op.create_table(
        "payment_requests",
        *scoped_columns(),
        sa.Column("link_id", U, nullable=False),
        sa.Column("invoice_id", U, nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("checkout_session_id", sa.String(255)),
        sa.Column("checkout_url", sa.String(1000)),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("payment_intent_id", sa.String(80)),
        sa.Column("method", sa.String(16)),
        sa.Column("applied_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("surplus_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("invoice_payment_id", U),
        sa.Column("paid_at", TS),
        sa.Column("closed_at", TS),
        sa.Column("last_checked_at", TS),
        sa.Column("last_error", sa.String(500)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("checkout_session_id"),
        composite_fk("link_id", "payment_links"),
        sa.CheckConstraint("status in ('creating','open','processing','paid','expired','failed')", name="status"),
        sa.CheckConstraint("amount_minor > 0", name="amount"),
        sa.CheckConstraint("applied_minor >= 0 and surplus_minor >= 0", name="split"),
    )
    op.create_index("ix_payment_requests_invoice", "payment_requests", ["business_id", "invoice_id"])
    op.create_index("ix_payment_requests_status", "payment_requests", ["business_id", "status"])
    scoped_table("payment_requests")


def downgrade() -> None:
    disable_business_rls("payment_requests")
    op.drop_index("ix_payment_requests_status", table_name="payment_requests")
    op.drop_index("ix_payment_requests_invoice", table_name="payment_requests")
    op.drop_table("payment_requests")
    disable_business_rls("payment_links")
    op.drop_index("uq_payment_links_active", table_name="payment_links")
    op.drop_table("payment_links")
    op.drop_index("uq_invoice_payments_external", table_name="invoice_payments")
    op.drop_constraint(op.f("ck_invoice_payments_external"), "invoice_payments", type_="check")
    op.drop_column("invoice_payments", "external_id")
    op.drop_column("invoice_payments", "provider")
