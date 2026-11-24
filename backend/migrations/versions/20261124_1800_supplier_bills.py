"""purchasing: supplier bills (accounts payable) and the payments made against them

Revision ID: 20261124_1800_supplier_bills
Revises: 20261124_1200_invoice_payments
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261124_1800_supplier_bills"
down_revision = "20261124_1200_invoice_payments"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "supplier_bills",
        *scoped_columns(),
        sa.Column("supplier_id", U, nullable=False),
        sa.Column("supplier_name", sa.String(200), nullable=False),
        sa.Column("purchase_order_id", U),
        sa.Column("supplier_reference", sa.String(100)),
        sa.Column("bill_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total_minor", sa.BigInteger, nullable=False),
        sa.Column("amount_paid_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("notes", sa.Text),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", U),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("supplier_id", "suppliers"),
        composite_fk("purchase_order_id", "purchase_orders"),
        sa.CheckConstraint("status in ('open','partially_paid','paid','void')", name="status"),
        sa.CheckConstraint(
            "total_minor > 0 and amount_paid_minor >= 0 and amount_paid_minor <= total_minor", name="amounts"
        ),
        sa.CheckConstraint("due_date is null or due_date >= bill_date", name="dates"),
    )
    op.create_index("ix_supplier_bills_supplier", "supplier_bills", ["business_id", "supplier_id"])
    op.create_index("ix_supplier_bills_purchase_order", "supplier_bills", ["business_id", "purchase_order_id"])
    scoped_table("supplier_bills")
    op.create_table(
        "supplier_bill_payments",
        *scoped_columns(),
        sa.Column("bill_id", U, nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("paid_on", sa.Date, nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(200)),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("bill_id", "supplier_bills"),
        sa.CheckConstraint("amount_minor > 0", name="amount"),
        sa.CheckConstraint("method in ('cash','card','bank_transfer','check','other')", name="method"),
    )
    op.create_index("ix_supplier_bill_payments_bill", "supplier_bill_payments", ["business_id", "bill_id"])
    scoped_table("supplier_bill_payments")


def downgrade() -> None:
    disable_business_rls("supplier_bill_payments")
    op.drop_index("ix_supplier_bill_payments_bill", table_name="supplier_bill_payments")
    op.drop_table("supplier_bill_payments")
    disable_business_rls("supplier_bills")
    op.drop_index("ix_supplier_bills_purchase_order", table_name="supplier_bills")
    op.drop_index("ix_supplier_bills_supplier", table_name="supplier_bills")
    op.drop_table("supplier_bills")
