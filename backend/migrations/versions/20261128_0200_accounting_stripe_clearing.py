"""accounting: a Stripe clearing account and a Stripe fees account; payouts sent as transfers and fees (M21 phase 4)

Revision ID: 20261128_0200_accounting_stripe
Revises: 20261128_0100_payments_fees_payouts
"""

import sqlalchemy as sa
from alembic import op

revision = "20261128_0200_accounting_stripe"
down_revision = "20261128_0100_payments_fees_payouts"
branch_labels = None
depends_on = None

ENTITY_TYPES_BEFORE = (
    "'customer','supplier','invoice','credit_note','credit_application','invoice_payment','bill','bill_payment','item',"
    "'customer_refund','supplier_credit'"
)
ENTITY_TYPES_AFTER = ENTITY_TYPES_BEFORE + ",'payout_transfer','payout_fees'"


def _entity_types(values: str) -> None:
    op.drop_constraint(op.f("ck_accounting_sync_records_entity_type"), "accounting_sync_records", type_="check")
    op.create_check_constraint(
        op.f("ck_accounting_sync_records_entity_type"), "accounting_sync_records", f"entity_type in ({values})"
    )


def upgrade() -> None:
    op.add_column("accounting_connections", sa.Column("stripe_clearing_account_id", sa.String(40)))
    op.add_column("accounting_connections", sa.Column("stripe_fees_account_id", sa.String(40)))
    _entity_types(ENTITY_TYPES_AFTER)


def downgrade() -> None:
    op.execute("delete from accounting_sync_records where entity_type in ('payout_transfer','payout_fees')")
    _entity_types(ENTITY_TYPES_BEFORE)
    op.drop_column("accounting_connections", "stripe_fees_account_id")
    op.drop_column("accounting_connections", "stripe_clearing_account_id")
