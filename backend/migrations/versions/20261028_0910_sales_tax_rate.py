"""org: exact default sales tax rate on business settings

Revision ID: 20261028_0910_sales_tax_rate
Revises: 20261028_0900_sales_core
Created (UTC): 2026-10-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261028_0910_sales_tax_rate"
down_revision = "20261028_0900_sales_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "business_settings",
        sa.Column("sales_tax_rate", sa.Numeric(6, 4), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint("sales_tax_rate", "business_settings", "sales_tax_rate between 0 and 1")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_business_settings_sales_tax_rate"), "business_settings", type_="check")
    op.drop_column("business_settings", "sales_tax_rate")
