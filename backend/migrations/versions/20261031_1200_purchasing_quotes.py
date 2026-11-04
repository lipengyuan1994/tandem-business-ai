"""Business-scoped supplier quote workflow and PO delivery tracking."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261031_1200_purchasing_quotes"
down_revision = "20261031_1100_invoice_reminders"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)
TABLES = ("quote_requests", "quote_request_lines", "quote_request_suppliers", "supplier_quotes", "supplier_quote_lines")


def upgrade() -> None:
    op.create_table(
        "quote_requests", *scoped_columns(),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("needed_by", sa.Date, nullable=False), sa.Column("reply_by", sa.Date, nullable=False),
        sa.Column("message", sa.String(2000)), sa.Column("created_by", U),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("status in ('draft','sent','closed','cancelled')", name="status"),
        sa.CheckConstraint("reply_by <= needed_by", name="dates"),
    )
    op.create_table(
        "quote_request_lines", *scoped_columns(),
        sa.Column("request_id", U, nullable=False), sa.Column("item_id", U, nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.UniqueConstraint("business_id", "id"), sa.UniqueConstraint("business_id", "request_id", "item_id"),
        composite_fk("request_id", "quote_requests"), composite_fk("item_id", "catalog_items"),
        sa.CheckConstraint("quantity > 0", name="quantity"),
    )
    op.create_table(
        "quote_request_suppliers", *scoped_columns(),
        sa.Column("request_id", U, nullable=False), sa.Column("supplier_id", U, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="invited"),
        sa.Column("sent_at", sa.DateTime(timezone=True)), sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.Column("last_followup_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("business_id", "id"), sa.UniqueConstraint("business_id", "request_id", "supplier_id"),
        composite_fk("request_id", "quote_requests"), composite_fk("supplier_id", "suppliers"),
        sa.CheckConstraint("status in ('invited','responded','declined','expired')", name="status"),
    )
    op.create_table(
        "supplier_quotes", *scoped_columns(),
        sa.Column("request_id", U, nullable=False), sa.Column("supplier_id", U, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False), sa.Column("lead_time_days", sa.Integer, nullable=False),
        sa.Column("min_quantity", sa.Numeric(14, 3)), sa.Column("valid_until", sa.Date), sa.Column("notes", sa.String(500)),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.UniqueConstraint("business_id", "id"), sa.UniqueConstraint("business_id", "request_id", "supplier_id"),
        sa.ForeignKeyConstraint(["business_id", "request_id", "supplier_id"],
            ["quote_request_suppliers.business_id", "quote_request_suppliers.request_id", "quote_request_suppliers.supplier_id"]),
        sa.CheckConstraint("source in ('form','staff','assistant_extracted')", name="source"),
        sa.CheckConstraint("status in ('received','accepted','rejected')", name="status"),
        sa.CheckConstraint("lead_time_days >= 0 and (min_quantity is null or min_quantity > 0)", name="values"),
    )
    op.create_table(
        "supplier_quote_lines", *scoped_columns(),
        sa.Column("quote_id", U, nullable=False), sa.Column("item_id", U, nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger, nullable=False), sa.Column("quantity_available", sa.Numeric(14, 3)),
        sa.UniqueConstraint("business_id", "id"), sa.UniqueConstraint("business_id", "quote_id", "item_id"),
        composite_fk("quote_id", "supplier_quotes"), composite_fk("item_id", "catalog_items"),
        sa.CheckConstraint("unit_price_minor >= 0 and (quantity_available is null or quantity_available >= 0)", name="values"),
    )
    for table in TABLES:
        scoped_table(table)
    for name, kind in (("sent_at", sa.DateTime(timezone=True)), ("expected_delivery_date", sa.Date()),
                       ("last_followup_at", sa.DateTime(timezone=True)), ("source_quote_id", U)):
        op.add_column("purchase_orders", sa.Column(name, kind))
    op.create_foreign_key("fk_purchase_orders_business_id_source_quote_id_supplier_quotes", "purchase_orders", "supplier_quotes",
                          ["business_id", "source_quote_id"], ["business_id", "id"])
    op.create_unique_constraint("uq_purchase_orders_business_id_source_quote_id", "purchase_orders", ["business_id", "source_quote_id"])


def downgrade() -> None:
    op.drop_constraint("uq_purchase_orders_business_id_source_quote_id", "purchase_orders", type_="unique")
    op.drop_constraint("fk_purchase_orders_business_id_source_quote_id_supplier_quotes", "purchase_orders", type_="foreignkey")
    for name in ("source_quote_id", "last_followup_at", "expected_delivery_date", "sent_at"):
        op.drop_column("purchase_orders", name)
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
