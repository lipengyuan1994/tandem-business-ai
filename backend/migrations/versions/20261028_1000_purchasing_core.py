"""Purchasing suppliers, POs, transactional numbering and receipt ledger.

Revision ID: 20261028_1000_purchasing_core
Revises: 20261028_0910_sales_tax_rate
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261028_1000_purchasing_core"
down_revision = "20261028_0910_sales_tax_rate"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)
TABLES = (
    "suppliers",
    "supplier_items",
    "purchase_order_sequences",
    "purchase_orders",
    "purchase_order_lines",
    "goods_receipts",
    "goods_receipt_lines",
)


def upgrade() -> None:
    op.create_table(
        "suppliers",
        *scoped_columns(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(60)),
        sa.Column("notes", sa.Text),
        sa.UniqueConstraint("business_id", "id"),
    )
    op.create_table(
        "supplier_items",
        *scoped_columns(),
        sa.Column("supplier_id", U, nullable=False),
        sa.Column("catalog_item_id", U, nullable=False),
        sa.Column("supplier_sku", sa.String(100)),
        sa.Column("last_price_minor", sa.BigInteger, nullable=False),
        sa.Column("lead_time_days", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "supplier_id", "catalog_item_id"),
        composite_fk("supplier_id", "suppliers"),
        composite_fk("catalog_item_id", "catalog_items"),
        sa.CheckConstraint("last_price_minor >= 0 and lead_time_days >= 0", name="values"),
    )
    op.create_table(
        "purchase_order_sequences",
        *scoped_columns(),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("next_value", sa.Integer, nullable=False, server_default="1"),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "year"),
        sa.CheckConstraint("next_value > 0", name="next_value"),
    )
    op.create_table(
        "purchase_orders",
        *scoped_columns(),
        sa.Column("supplier_id", U, nullable=False),
        sa.Column("supplier_name", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("number", sa.String(32)),
        sa.Column("approved_date", sa.Date),
        sa.Column("notes", sa.Text),
        sa.Column("total_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("source_draft_id", U),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "number"),
        sa.UniqueConstraint("business_id", "source_draft_id"),
        composite_fk("supplier_id", "suppliers"),
        composite_fk("source_draft_id", "reorder_drafts"),
        sa.CheckConstraint(
            "status in ('draft','approved','sent','partially_received','received','cancelled')", name="status"
        ),
        sa.CheckConstraint("total_minor >= 0", name="total"),
        sa.CheckConstraint(
            "(status in ('draft','cancelled')) or (number is not null and approved_date is not null)", name="number"
        ),
    )
    op.create_table(
        "purchase_order_lines",
        *scoped_columns(),
        sa.Column("purchase_order_id", U, nullable=False),
        sa.Column("catalog_item_id", U, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("received_quantity", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("unit_cost_minor", sa.BigInteger, nullable=False),
        sa.Column("line_total_minor", sa.BigInteger, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "purchase_order_id", "position"),
        composite_fk("purchase_order_id", "purchase_orders"),
        composite_fk("catalog_item_id", "catalog_items"),
        sa.CheckConstraint(
            "quantity > 0 and received_quantity >= 0 and unit_cost_minor >= 0 and line_total_minor >= 0", name="values"
        ),
    )
    op.create_table(
        "goods_receipts",
        *scoped_columns(),
        sa.Column("purchase_order_id", U, nullable=False),
        sa.Column("received_by", U),
        sa.Column("override_reason", sa.String(1000)),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("purchase_order_id", "purchase_orders"),
    )
    op.create_table(
        "goods_receipt_lines",
        *scoped_columns(),
        sa.Column("receipt_id", U, nullable=False),
        sa.Column("po_line_id", U, nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "receipt_id", "po_line_id"),
        composite_fk("receipt_id", "goods_receipts"),
        composite_fk("po_line_id", "purchase_order_lines"),
        sa.CheckConstraint("quantity > 0", name="quantity"),
    )
    for table in TABLES:
        scoped_table(table, append=table in ("goods_receipts", "goods_receipt_lines"))
    for table, column in (
        ("purchase_orders", "supplier_id"),
        ("purchase_order_lines", "purchase_order_id"),
        ("goods_receipts", "purchase_order_id"),
        ("goods_receipt_lines", "receipt_id"),
    ):
        op.create_index(f"ix_{table}_{column}", table, ["business_id", column])


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
