"""crm: customers, contact points, consent records (append-only), notes, tags, timeline

Revision ID: 20261025_1610_crm_core
Revises: 20261025_1600_catalog_core
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261025_1610_crm_core"
down_revision = "20261025_1600_catalog_core"
branch_labels = None
depends_on = None

TABLES = ("customers", "contact_points", "consent_records", "customer_notes", "tags", "customer_tags", "timeline_entries")
U = UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "customers", *scoped_columns(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("given_name", sa.String(100)), sa.Column("family_name", sa.String(100)),
        sa.Column("organization_name", sa.String(200)), sa.Column("preferred_language", sa.String(20)),
        sa.Column("timezone", sa.String(64)),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("merged_into_id", U), sa.Column("last_interaction_at", TS), sa.Column("created_by", U),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("kind in ('person','organization')", name="kind"),
        sa.CheckConstraint("status in ('active','archived','merged','erased')", name="status"),
        sa.CheckConstraint("source in ('staff','public_chat','import','assistant')", name="source"),
    )
    op.create_index("ix_customers_name_trgm", "customers", [sa.text("lower(display_name) gin_trgm_ops")], postgresql_using="gin")
    op.create_table(
        "contact_points", *scoped_columns(),
        sa.Column("customer_id", U, nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("value", sa.String(320), nullable=False),
        sa.Column("value_normalized", sa.String(320), nullable=False),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("verified_at", TS),
        sa.UniqueConstraint("business_id", "id"), composite_fk("customer_id", "customers"),
        sa.CheckConstraint("type in ('email','phone')", name="type"),
    )
    op.create_index("uq_contact_points_active", "contact_points", ["business_id", "type", "value_normalized"], unique=True,
                    postgresql_where=sa.text("is_active"))
    op.create_table(
        "consent_records", *scoped_columns(),
        sa.Column("customer_id", U, nullable=False),
        sa.Column("channel", sa.String(8), nullable=False), sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), nullable=False), sa.Column("source", sa.String(20), nullable=False),
        sa.Column("evidence", sa.String(500), nullable=False), sa.Column("recorded_by", U),
        sa.Column("recorded_at", TS, nullable=False),
        sa.UniqueConstraint("business_id", "id"), composite_fk("customer_id", "customers"),
        sa.CheckConstraint("channel in ('email','sms')", name="channel"),
        sa.CheckConstraint("purpose in ('marketing','service_updates')", name="purpose"),
        sa.CheckConstraint("status in ('granted','denied','withdrawn')", name="status"),
        sa.CheckConstraint("source in ('form','staff','import','unsubscribe_link')", name="source"),
    )
    op.create_index("ix_consent_latest", "consent_records", ["business_id", "customer_id", "channel", "purpose", "recorded_at"])
    op.create_table(
        "customer_notes", *scoped_columns(),
        sa.Column("customer_id", U, nullable=False), sa.Column("body", sa.Text, nullable=False),
        sa.Column("author_id", U), sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("business_id", "id"), composite_fk("customer_id", "customers"),
    )
    op.create_table(
        "tags", *scoped_columns(), sa.Column("name", sa.String(60), nullable=False), sa.Column("color", sa.String(16)),
        sa.UniqueConstraint("business_id", "id"),
    )
    op.create_index("uq_tags_name", "tags", ["business_id", "name"], unique=True)
    op.create_table(
        "customer_tags", *scoped_columns(), sa.Column("customer_id", U, nullable=False), sa.Column("tag_id", U, nullable=False),
        sa.UniqueConstraint("business_id", "id"), composite_fk("customer_id", "customers"), composite_fk("tag_id", "tags"),
    )
    op.create_index("uq_customer_tags", "customer_tags", ["customer_id", "tag_id"], unique=True)
    op.create_table(
        "timeline_entries", *scoped_columns(),
        sa.Column("customer_id", U, nullable=False), sa.Column("occurred_at", TS, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("ref_module", sa.String(30), nullable=False),
        sa.Column("ref_type", sa.String(30), nullable=False), sa.Column("ref_id", U, nullable=False),
        sa.Column("summary", sa.String(200), nullable=False),
        sa.UniqueConstraint("business_id", "id"), composite_fk("customer_id", "customers"),
        sa.CheckConstraint("kind in ('message','appointment','reservation','note','campaign','proposal','order','other')", name="kind"),
    )
    op.create_index("uq_timeline_ref", "timeline_entries", ["business_id", "ref_type", "ref_id", "kind"], unique=True)
    op.create_index("ix_timeline_customer", "timeline_entries", ["business_id", "customer_id", "occurred_at"])
    for table in TABLES:
        scoped_table(table, append=table == "consent_records")


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
