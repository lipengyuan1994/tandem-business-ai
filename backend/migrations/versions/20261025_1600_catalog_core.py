"""catalog: items (services and products), service parts

Revision ID: 20261025_1600_catalog_core
Revises: 20261025_1530_notifications_core
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261025_1600_catalog_core"
down_revision = "20261025_1530_notifications_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_items",
        *scoped_columns(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("sku", sa.String(64)),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("unit", sa.String(10), nullable=False, server_default="each"),
        sa.Column("price_minor", sa.BigInteger),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("tax_code", sa.String(32)),
        sa.Column("duration_minutes", sa.Integer),
        sa.Column("buffer_minutes", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("resource_kind", sa.String(12)),
        sa.Column("is_stock_tracked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_bookable_online", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("kind in ('service','product')", name="kind"),
        sa.CheckConstraint("status in ('active','archived')", name="status"),
        sa.CheckConstraint(
            "(kind = 'service' and duration_minutes between 5 and 1440 and resource_kind is not null and not is_stock_tracked)"
            " or (kind = 'product' and duration_minutes is null)",
            name="kind_fields",
        ),
    )
    op.create_index("uq_catalog_items_sku", "catalog_items", ["business_id", "sku"], unique=True, postgresql_where=sa.text("sku is not null"))
    op.create_index("ix_catalog_items_name_trgm", "catalog_items", [sa.text("lower(name) gin_trgm_ops")], postgresql_using="gin")
    op.create_table(
        "service_parts",
        *scoped_columns(),
        sa.Column("service_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("service_id", "catalog_items"),
        composite_fk("item_id", "catalog_items"),
        sa.CheckConstraint("quantity > 0", name="quantity"),
    )
    op.create_index("uq_service_parts", "service_parts", ["service_id", "item_id"], unique=True)
    scoped_table("catalog_items")
    scoped_table("service_parts")


def downgrade() -> None:
    for table in ("service_parts", "catalog_items"):
        disable_business_rls(table)
        op.drop_table(table)
