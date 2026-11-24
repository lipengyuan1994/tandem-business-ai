"""sales: partial invoice payments and the amount paid on each invoice

Existing paid invoices get one payment row for their full total (method "other", dated on the business-local day they
were marked paid), so payment history, balances and receivables aging agree from the start.

Revision ID: 20261124_1200_invoice_payments
Revises: 20261107_1700_demo_reviewer
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261124_1200_invoice_payments"
down_revision = "20261107_1700_demo_reviewer"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("invoices", sa.Column("amount_paid_minor", sa.BigInteger, nullable=False, server_default="0"))
    op.create_table(
        "invoice_payments",
        *scoped_columns(),
        sa.Column("invoice_id", U, nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("paid_on", sa.Date, nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(200)),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("invoice_id", "invoices"),
        sa.CheckConstraint("amount_minor > 0", name="amount"),
        sa.CheckConstraint("method in ('cash','card','bank_transfer','check','other')", name="method"),
    )
    op.create_index("ix_invoice_payments_invoice", "invoice_payments", ["business_id", "invoice_id"])
    scoped_table("invoice_payments")
    # The migration role bypasses row-level security, so the backfill sees every business.
    op.execute(
        """
        insert into invoice_payments (id, business_id, invoice_id, amount_minor, paid_on, method)
        select gen_random_uuid(), i.business_id, i.id, i.total_minor,
               coalesce((i.paid_at at time zone b.timezone)::date, i.issue_date, i.created_at::date), 'other'
          from invoices i join businesses b on b.id = i.business_id
         where i.status = 'paid' and i.total_minor > 0
        """
    )
    op.execute("update invoices set amount_paid_minor = total_minor where status = 'paid'")
    op.create_check_constraint("amount_paid", "invoices", "amount_paid_minor >= 0 and amount_paid_minor <= total_minor")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_invoices_amount_paid"), "invoices", type_="check")
    disable_business_rls("invoice_payments")
    op.drop_index("ix_invoice_payments_invoice", table_name="invoice_payments")
    op.drop_table("invoice_payments")
    op.drop_column("invoices", "amount_paid_minor")
