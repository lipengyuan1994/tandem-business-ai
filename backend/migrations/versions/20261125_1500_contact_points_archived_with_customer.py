"""crm: remember which contact points archiving a customer switched off, so restoring brings them back

Revision ID: 20261125_1500_cp_archived
Revises: 20261124_2000_accounting_sync
"""

import sqlalchemy as sa
from alembic import op

revision = "20261125_1500_cp_archived"
down_revision = "20261124_2000_accounting_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contact_points",
        sa.Column("archived_with_customer", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    # Customers archived before this column existed: archiving kept the primary flag and removing a contact point
    # clears it, so the primary contact points of archived customers are the ones archiving switched off.
    op.execute(
        """
        update contact_points cp set archived_with_customer = true
          from customers c
         where c.business_id = cp.business_id and c.id = cp.customer_id
           and c.status = 'archived' and not cp.is_active and cp.is_primary
        """
    )


def downgrade() -> None:
    op.drop_column("contact_points", "archived_with_customer")
