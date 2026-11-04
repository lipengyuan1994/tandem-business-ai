"""inventory: stock locations, levels, movements (append-only), reservations, reorder rules and drafts

Revision ID: 20261025_1620_inventory_core
Revises: 20261025_1610_crm_core
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261025_1620_inventory_core"
down_revision = "20261025_1610_crm_core"
branch_labels = None
depends_on = None

U, Q, TS = UUID(as_uuid=True), sa.Numeric(14, 3), sa.DateTime(timezone=True)
TABLES = ("stock_locations", "stock_levels", "stock_movements", "stock_reservations", "reorder_rules", "reorder_drafts", "reorder_draft_lines")


def upgrade() -> None:
    op.create_table("stock_locations", *scoped_columns(), sa.Column("name", sa.String(120), nullable=False), sa.Column("location_id", U),
                    sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")), sa.UniqueConstraint("business_id", "id"))
    op.create_index("uq_stock_locations_default", "stock_locations", ["business_id"], unique=True, postgresql_where=sa.text("is_default"))
    item_loc = lambda: [composite_fk("item_id", "catalog_items"), composite_fk("stock_location_id", "stock_locations")]  # noqa: E731
    op.create_table("stock_levels", *scoped_columns(), sa.Column("item_id", U, nullable=False), sa.Column("stock_location_id", U, nullable=False),
                    sa.Column("on_hand", Q, nullable=False, server_default="0"), sa.Column("reserved", Q, nullable=False, server_default="0"),
                    sa.UniqueConstraint("business_id", "id"), *item_loc(),
                    sa.CheckConstraint("on_hand >= 0", name="on_hand"), sa.CheckConstraint("reserved >= 0", name="reserved"))
    op.create_index("uq_stock_levels", "stock_levels", ["business_id", "item_id", "stock_location_id"], unique=True)
    op.create_table("stock_movements", *scoped_columns(), sa.Column("item_id", U, nullable=False), sa.Column("stock_location_id", U, nullable=False),
                    sa.Column("quantity", Q, nullable=False), sa.Column("reason", sa.String(20), nullable=False),
                    sa.Column("ref_type", sa.String(30)), sa.Column("ref_id", U), sa.Column("note", sa.Text), sa.Column("unit_cost_minor", sa.BigInteger),
                    sa.Column("occurred_at", TS, nullable=False), sa.Column("recorded_by", U), sa.Column("proposal_id", U),
                    sa.UniqueConstraint("business_id", "id"), *item_loc(), sa.CheckConstraint("quantity <> 0", name="quantity"),
                    sa.CheckConstraint("reason in ('receipt','consumption','sale','adjustment_gain','adjustment_loss','count_correction',"
                                       "'transfer_in','transfer_out','return')", name="reason"))
    op.create_index("ix_stock_movements_item", "stock_movements", ["business_id", "item_id", "occurred_at"])
    op.create_table("stock_reservations", *scoped_columns(), sa.Column("item_id", U, nullable=False), sa.Column("stock_location_id", U, nullable=False),
                    sa.Column("quantity", Q, nullable=False), sa.Column("status", sa.String(10), nullable=False, server_default="active"),
                    sa.Column("ref_type", sa.String(20), nullable=False), sa.Column("ref_id", U, nullable=False),
                    sa.Column("released_at", TS), sa.Column("consumed_movement_id", U),
                    sa.UniqueConstraint("business_id", "id"), *item_loc(), sa.CheckConstraint("quantity > 0", name="quantity"),
                    sa.CheckConstraint("status in ('active','released','consumed')", name="status"),
                    sa.CheckConstraint("ref_type in ('appointment','sales_order')", name="ref_type"))
    op.create_index("ix_stock_reservations_ref", "stock_reservations", ["business_id", "ref_type", "ref_id"])
    op.create_table("reorder_rules", *scoped_columns(), sa.Column("item_id", U, nullable=False), sa.Column("stock_location_id", U, nullable=False),
                    sa.Column("reorder_point", Q, nullable=False), sa.Column("target_level", Q, nullable=False),
                    sa.Column("lead_time_days", sa.Integer, nullable=False, server_default="0"), sa.Column("supplier_hint", sa.String(200)),
                    sa.UniqueConstraint("business_id", "id"), *item_loc(),
                    sa.CheckConstraint("reorder_point >= 0 and target_level > reorder_point", name="levels"))
    op.create_index("uq_reorder_rules", "reorder_rules", ["business_id", "item_id", "stock_location_id"], unique=True)
    op.create_table("reorder_drafts", *scoped_columns(), sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
                    sa.Column("note", sa.Text), sa.Column("proposal_id", U), sa.UniqueConstraint("business_id", "id"),
                    sa.CheckConstraint("status in ('draft','sent_to_purchasing','cancelled')", name="status"))
    op.create_table("reorder_draft_lines", *scoped_columns(), sa.Column("draft_id", U, nullable=False), sa.Column("item_id", U, nullable=False),
                    sa.Column("quantity", Q, nullable=False), sa.Column("reason_text", sa.String(300)), sa.UniqueConstraint("business_id", "id"),
                    composite_fk("draft_id", "reorder_drafts"), composite_fk("item_id", "catalog_items"),
                    sa.CheckConstraint("quantity > 0", name="quantity"))
    for table in TABLES:
        scoped_table(table, append=table == "stock_movements")


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
