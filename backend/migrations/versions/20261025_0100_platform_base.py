"""platform base: extensions, updated_at trigger function, businesses table (owned by org)

Revision ID: 20261025_0100_platform_base
Revises:
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from migrations.helpers import create_updated_at_trigger, disable_business_rls, enable_business_rls

revision = "20261025_0100_platform_base"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Descriptive revision IDs (YYYYMMDD_HHMM_<module>_<what>) exceed Alembic's default varchar(32).
    op.execute("alter table alembic_version alter column version_num type varchar(128)")
    # Created by the superuser in postgres-init.sh; IF NOT EXISTS keeps this a no-op there.
    for ext in ("btree_gist", "pg_trgm", "vector"):
        op.execute(f"create extension if not exists {ext}")
    op.execute(
        """
        create or replace function set_updated_at() returns trigger language plpgsql as $$
        begin new.updated_at = now(); return new; end $$
        """
    )
    op.create_table(
        "businesses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("legal_name", sa.String(200)),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("locale", sa.String(16), nullable=False, server_default="en-US"),
        sa.Column("country", sa.String(2)),
        sa.Column("public_profile", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("slug ~ '^[a-z0-9-]{3,40}$'", name="slug"),
        sa.CheckConstraint("status in ('active','suspended')", name="status"),
    )
    enable_business_rls("businesses", column="id")  # P2-02 widens this to the user's memberships
    create_updated_at_trigger("businesses")
    op.execute(
        """
        create function public_business_by_slug(p_slug text)
        returns table (id uuid, name text, timezone text, public_profile jsonb)
        language sql stable security definer set search_path = public as $$
          select b.id, b.name::text, b.timezone::text, b.public_profile
            from businesses b where b.slug = p_slug and b.status = 'active'
        $$
        """
    )
    op.execute("revoke all on function public_business_by_slug(text) from public")
    op.execute("grant execute on function public_business_by_slug(text) to smb_app")


def downgrade() -> None:
    op.execute("drop function if exists public_business_by_slug(text)")
    disable_business_rls("businesses")
    op.drop_table("businesses")
    op.execute("drop function if exists set_updated_at()")
