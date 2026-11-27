"""payments: settings for pay links in reminders and drafted surplus refunds; Dashboard refunds can be matched to a
credit note (M21 phase 5)

Revision ID: 20261128_0300_payments_agent
Revises: 20261128_0200_accounting_stripe
"""

import sqlalchemy as sa
from alembic import op

revision = "20261128_0300_payments_agent"
down_revision = "20261128_0200_accounting_stripe"
branch_labels = None
depends_on = None


OLD_CHECK = "(source = 'credit_note') = (invoice_refund_id is not null)"
NEW_CHECK = (
    "(source = 'credit_note' and invoice_refund_id is not null)"
    " or (source = 'surplus' and invoice_refund_id is null) or source = 'dashboard'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_payment_refunds_credit_note"), "payment_refunds", type_="check")
    op.create_check_constraint("credit_note", "payment_refunds", NEW_CHECK)
    op.add_column(
        "payment_connections", sa.Column("reminder_links", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "payment_connections", sa.Column("draft_refunds", sa.Boolean(), nullable=False, server_default=sa.true())
    )


def downgrade() -> None:
    op.execute("update payment_refunds set invoice_refund_id = null where source = 'dashboard'")
    op.drop_constraint(op.f("ck_payment_refunds_credit_note"), "payment_refunds", type_="check")
    op.create_check_constraint("credit_note", "payment_refunds", OLD_CHECK)
    op.drop_column("payment_connections", "draft_refunds")
    op.drop_column("payment_connections", "reminder_links")
