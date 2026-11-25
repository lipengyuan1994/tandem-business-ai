"""accounting: QuickBooks Online connection per business and the log of records pushed to it

Revision ID: 20261124_2000_accounting_sync
Revises: 20261124_1800_supplier_bills
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import disable_business_rls, scoped_columns, scoped_table

revision = "20261124_2000_accounting_sync"
down_revision = "20261124_1800_supplier_bills"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "accounting_connections",
        *scoped_columns(),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("environment", sa.String(12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("realm_id", sa.String(40)),
        sa.Column("company_name", sa.String(200)),
        sa.Column("country", sa.String(8)),
        sa.Column("home_currency", sa.String(3)),
        sa.Column("access_token_sealed", sa.Text),
        sa.Column("refresh_token_sealed", sa.Text),
        sa.Column("access_expires_at", TS),
        sa.Column("refresh_expires_at", TS),
        sa.Column("oauth_state_hash", sa.String(64)),
        sa.Column("oauth_state_expires_at", TS),
        sa.Column("connected_by", U),
        sa.Column("connected_at", TS),
        sa.Column("sync_from", sa.Date),
        sa.Column("income_account_id", sa.String(40)),
        sa.Column("receivable_account_id", sa.String(40)),
        sa.Column("deposit_account_id", sa.String(40)),
        sa.Column("tax_account_id", sa.String(40)),
        sa.Column("expense_account_id", sa.String(40)),
        sa.Column("payable_account_id", sa.String(40)),
        sa.Column("bank_account_id", sa.String(40)),
        sa.Column("auto_sync", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("sync_lease_until", TS),
        sa.Column("last_sync_started_at", TS),
        sa.Column("last_sync_finished_at", TS),
        sa.Column("last_sync_error", sa.String(500)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id"),
        sa.CheckConstraint("provider in ('quickbooks')", name="provider"),
        sa.CheckConstraint("environment in ('sandbox','production')", name="environment"),
        sa.CheckConstraint(
            "status in ('pending','connected','needs_reconnect','disconnected')", name="status"
        ),
    )
    scoped_table("accounting_connections")
    op.create_table(
        "accounting_sync_records",
        *scoped_columns(),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("realm_id", sa.String(40), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("local_id", sa.String(64), nullable=False),
        sa.Column("local_version", sa.Integer),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("remote_id", sa.String(40)),
        sa.Column("remote_sync_token", sa.String(20)),
        sa.Column("remote_state", sa.String(12)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500)),
        sa.Column("last_attempt_at", TS),
        sa.Column("next_attempt_at", TS),
        sa.Column("synced_at", TS),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "provider", "realm_id", "entity_type", "local_id"),
        sa.CheckConstraint(
            "entity_type in ('customer','supplier','invoice','credit_note','credit_application',"
            "'invoice_payment','bill','bill_payment','item')",
            name="entity_type",
        ),
        sa.CheckConstraint("status in ('synced','failed','skipped')", name="status"),
        sa.CheckConstraint("remote_state is null or remote_state in ('active','voided','deleted')", name="remote_state"),
    )
    op.create_index(
        "ix_accounting_sync_records_status", "accounting_sync_records", ["business_id", "realm_id", "status"]
    )
    scoped_table("accounting_sync_records")


def downgrade() -> None:
    disable_business_rls("accounting_sync_records")
    op.drop_index("ix_accounting_sync_records_status", table_name="accounting_sync_records")
    op.drop_table("accounting_sync_records")
    disable_business_rls("accounting_connections")
    op.drop_table("accounting_connections")
