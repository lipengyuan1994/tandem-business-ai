"""org: locations, opening hours, business settings

Revision ID: 20261025_1400_org_locations_settings
Revises: 20261025_0110_platform_tables
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from migrations.helpers import create_updated_at_trigger, disable_business_rls, enable_business_rls

revision = "20261025_1400_org_locations_settings"
down_revision = "20261025_0110_platform_tables"
branch_labels = None
depends_on = None

TABLES = ("locations", "opening_hours", "business_settings")


def _base() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "locations",
        *_base(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("address", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("business_id", "id"),
    )
    op.create_index("uq_locations_one_default", "locations", ["business_id"], unique=True, postgresql_where=sa.text("is_default"))
    op.create_table(
        "opening_hours",
        *_base(),
        sa.Column("location_id", UUID(as_uuid=True), nullable=False),
        sa.Column("weekday", sa.SmallInteger, nullable=False),
        sa.Column("opens", sa.Time, nullable=False),
        sa.Column("closes", sa.Time, nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        sa.ForeignKeyConstraint(["business_id", "location_id"], ["locations.business_id", "locations.id"], ondelete="CASCADE"),
        sa.CheckConstraint("weekday between 0 and 6", name="weekday"),
        sa.CheckConstraint("closes > opens", name="closes_after_opens"),
    )
    op.create_table(
        "business_settings",
        *_base(),
        sa.Column("settings", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("business_id", "id"),
    )
    op.create_index("uq_business_settings_business", "business_settings", ["business_id"], unique=True)
    for table in TABLES:
        enable_business_rls(table)
        create_updated_at_trigger(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
