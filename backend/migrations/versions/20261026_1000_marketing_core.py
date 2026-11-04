"""marketing: segments, campaigns, immutable revisions, audience snapshots, suppressions (append-only)

Revision ID: 20261026_1000_marketing_core
Revises: 20261025_1640_scheduling_core
Created (UTC): 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261026_1000_marketing_core"
down_revision = "20261025_1640_scheduling_core"
branch_labels = None
depends_on = None

TABLES = ("segments", "campaigns", "campaign_revisions", "audience_snapshots", "audience_members", "suppressions")
U = UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "segments", *scoped_columns(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.UniqueConstraint("business_id", "id"),
    )
    op.create_table(
        "campaigns", *scoped_columns(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("purpose", sa.Text),
        sa.Column("channel", sa.String(8), nullable=False, server_default="email"),
        sa.Column("segment_id", U, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("current_revision_id", U), sa.Column("submitted_snapshot_id", U),
        sa.Column("approved_snapshot_id", U), sa.Column("proposal_id", U), sa.Column("sent_at", TS),
        sa.UniqueConstraint("business_id", "id"), composite_fk("segment_id", "segments"),
        sa.CheckConstraint("channel in ('email')", name="channel"),
        sa.CheckConstraint("status in ('draft','in_review','approved','sending','sent','cancelled')", name="status"),
    )
    op.create_table(
        "campaign_revisions", *scoped_columns(),
        sa.Column("campaign_id", U, nullable=False),
        sa.Column("revision_no", sa.Integer, nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body_markdown", sa.Text, nullable=False),
        sa.Column("created_by_kind", sa.String(10), nullable=False),
        sa.Column("created_by", U), sa.Column("ai_run_id", U),
        sa.UniqueConstraint("business_id", "id"), composite_fk("campaign_id", "campaigns"),
        sa.CheckConstraint("created_by_kind in ('staff','assistant')", name="created_by_kind"),
    )
    op.create_index("uq_campaign_revisions_no", "campaign_revisions", ["campaign_id", "revision_no"], unique=True)
    op.create_table(
        "audience_snapshots", *scoped_columns(),
        sa.Column("campaign_id", U, nullable=False),
        sa.Column("segment_definition", JSONB, nullable=False),
        sa.Column("computed_at", TS, nullable=False),
        sa.Column("eligible_count", sa.Integer, nullable=False),
        sa.Column("excluded_counts", JSONB, nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("business_id", "id"), composite_fk("campaign_id", "campaigns"),
    )
    op.create_table(
        "audience_members", *scoped_columns(),
        sa.Column("snapshot_id", U, nullable=False),
        sa.Column("customer_id", U, nullable=False),
        sa.Column("contact_point_id", U, nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("snapshot_id", "audience_snapshots"), composite_fk("customer_id", "customers"),
        composite_fk("contact_point_id", "contact_points"),
    )
    op.create_index("uq_audience_members", "audience_members", ["snapshot_id", "customer_id"], unique=True)
    op.create_table(
        "suppressions", *scoped_columns(),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("value_normalized", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(12), nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("channel in ('email')", name="channel"),
        sa.CheckConstraint("reason in ('unsubscribed','bounced','complaint','manual')", name="reason"),
    )
    op.create_index("uq_suppressions", "suppressions", ["business_id", "channel", "value_normalized"], unique=True)
    for table in TABLES:
        scoped_table(table, append=table in ("suppressions", "campaign_revisions", "audience_members"))


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
