"""sales: quotes, sales orders, invoices, credit notes and transactional numbering

Revision ID: 20261028_0900_sales_core
Revises: 20261027_1500_erasure_hardening
Created (UTC): 2026-10-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261028_0900_sales_core"
down_revision = "20261027_1500_erasure_hardening"
branch_labels = None
depends_on = None

TABLES = (
    "document_sequences",
    "quotes",
    "quote_lines",
    "sales_orders",
    "sales_order_lines",
    "invoices",
    "invoice_lines",
)
U = UUID(as_uuid=True)


def document_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        *scoped_columns(),
        sa.Column("customer_id", U, nullable=False),
        sa.Column("bill_to_name", sa.String(200), nullable=False),
        sa.Column("bill_to_email", sa.String(320)),
        sa.Column("bill_to_address", sa.Text),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("number", sa.String(32)),
        sa.Column("issue_date", sa.Date),
        sa.Column("due_date", sa.Date),
        sa.Column("notes", sa.Text),
        sa.Column("subtotal_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tax_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("total_minor", sa.BigInteger, nullable=False, server_default="0"),
    ]


def document_constraints(statuses: str) -> list[sa.Constraint]:
    return [
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("customer_id", "customers"),
        sa.UniqueConstraint("business_id", "number"),
        sa.CheckConstraint(f"status in ({statuses})", name="status"),
        sa.CheckConstraint(
            "subtotal_minor >= 0 and tax_minor >= 0 and total_minor = subtotal_minor + tax_minor", name="totals"
        ),
        sa.CheckConstraint(
            "(status = 'draft' and number is null) or (status <> 'draft' and number is not null)", name="number"
        ),
    ]


def create_lines(table: str, parent: str) -> None:
    op.create_table(
        table,
        *scoped_columns(),
        sa.Column("document_id", U, nullable=False),
        sa.Column("catalog_item_id", U),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger, nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("line_subtotal_minor", sa.BigInteger, nullable=False),
        sa.Column("line_tax_minor", sa.BigInteger, nullable=False),
        sa.Column("line_total_minor", sa.BigInteger, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("document_id", parent),
        composite_fk("catalog_item_id", "catalog_items"),
        sa.UniqueConstraint("business_id", "document_id", "position"),
        sa.CheckConstraint("quantity > 0 and unit_price_minor >= 0 and tax_rate between 0 and 1", name="values"),
        sa.CheckConstraint(
            "line_subtotal_minor >= 0 and line_tax_minor >= 0 and line_total_minor = line_subtotal_minor + line_tax_minor",
            name="totals",
        ),
    )
    op.create_index(f"ix_{table}_document", table, ["business_id", "document_id"])


def upgrade() -> None:
    op.create_table(
        "document_sequences",
        *scoped_columns(),
        sa.Column("doc_type", sa.String(3), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("next_value", sa.Integer, nullable=False, server_default="1"),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "doc_type", "year"),
        sa.CheckConstraint("doc_type in ('QUO','SO','INV','CN')", name="doc_type"),
        sa.CheckConstraint("next_value > 0", name="next_value"),
    )
    op.create_table(
        "quotes", *document_columns(), *document_constraints("'draft','sent','accepted','declined','expired'")
    )
    create_lines("quote_lines", "quotes")
    op.create_table(
        "sales_orders",
        *document_columns(),
        sa.Column("source_quote_id", U),
        *document_constraints("'draft','confirmed','fulfilled','cancelled'"),
        composite_fk("source_quote_id", "quotes"),
        sa.UniqueConstraint("business_id", "source_quote_id"),
    )
    create_lines("sales_order_lines", "sales_orders")
    op.create_table(
        "invoices",
        *document_columns(),
        sa.Column("kind", sa.String(12), nullable=False, server_default="invoice"),
        sa.Column("credit_of_invoice_id", U),
        sa.Column("source_appointment_id", U),
        sa.Column("source_order_id", U),
        sa.Column("source_quote_id", U),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        *document_constraints("'draft','issued','paid','void'"),
        composite_fk("credit_of_invoice_id", "invoices"),
        composite_fk("source_appointment_id", "appointments"),
        composite_fk("source_order_id", "sales_orders"),
        composite_fk("source_quote_id", "quotes"),
        sa.UniqueConstraint("business_id", "credit_of_invoice_id"),
        sa.UniqueConstraint("business_id", "source_order_id"),
        sa.UniqueConstraint("business_id", "source_appointment_id"),
        sa.CheckConstraint(
            "(kind = 'invoice' and credit_of_invoice_id is null) or (kind = 'credit_note' and credit_of_invoice_id is not null and status = 'issued')",
            name="kind",
        ),
    )
    create_lines("invoice_lines", "invoices")
    for table in TABLES:
        # Draft lines must support replacement; document state restrictions live in the service.
        scoped_table(table)
    for table in ("quotes", "sales_orders", "invoices"):
        op.create_index(f"ix_{table}_customer", table, ["business_id", "customer_id"])


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
