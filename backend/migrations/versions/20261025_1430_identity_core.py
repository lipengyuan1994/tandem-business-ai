"""identity: users, sessions, OIDC login states, memberships, role grants, invitations

Revision ID: 20261025_1430_identity_core
Revises: 20261025_1400_org_locations_settings
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from migrations.helpers import TENANT, create_updated_at_trigger, disable_business_rls, enable_business_rls

revision = "20261025_1430_identity_core"
down_revision = "20261025_1400_org_locations_settings"
branch_labels = None
depends_on = None

USER = "nullif(current_setting('app.user_id', true), '')::uuid"
NOW = sa.func.now()


def _ts(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=None if nullable else NOW)


def _scoped() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        _ts("created_at"),
        _ts("updated_at"),
    ]


def upgrade() -> None:
    # Global tables (no business_id): accessed only through identity's repository.
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("oidc_subject", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        _ts("last_login_at", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("status in ('active','disabled')", name="status"),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("active_business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id")),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        _ts("last_seen_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("revoked_at", nullable=True),
        sa.Column("ip_hash", sa.String(64)),
        sa.Column("user_agent", sa.String(300)),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_sessions_user", "sessions", ["user_id"])
    op.create_table(
        "oidc_login_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("return_to", sa.String(500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Business-scoped tables.
    op.create_table(
        "memberships",
        *_scoped(),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("title", sa.String(120)),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("status in ('invited','active','suspended','removed')", name="status"),
    )
    op.create_index("uq_memberships_business_user", "memberships", ["business_id", "user_id"], unique=True)
    op.create_table(
        "role_grants",
        *_scoped(),
        sa.Column("membership_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("granted_by", UUID(as_uuid=True)),
        _ts("revoked_at", nullable=True),
        sa.UniqueConstraint("business_id", "id"),
        sa.ForeignKeyConstraint(["business_id", "membership_id"], ["memberships.business_id", "memberships.id"]),
        sa.CheckConstraint(
            "role in ('owner','admin','operator','inventory_manager','marketing_manager','auditor')", name="role"
        ),
    )
    op.create_index(
        "uq_role_grants_active", "role_grants", ["membership_id", "role"], unique=True,
        postgresql_where=sa.text("revoked_at is null"),
    )
    op.create_table(
        "invitations",
        *_scoped(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("roles", ARRAY(sa.String(32)), nullable=False),
        sa.Column("invited_by", UUID(as_uuid=True)),
        sa.Column("keycloak_user_id", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _ts("accepted_at", nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("status in ('pending','accepted','revoked','expired')", name="status"),
    )
    op.create_index("ix_invitations_email", "invitations", [sa.text("lower(email)")])
    for table in ("users", "sessions", "memberships", "role_grants", "invitations"):
        create_updated_at_trigger(table)

    # Row-level security. Memberships and grants are also visible to the user they belong to (business picker).
    for table in ("memberships", "role_grants", "invitations"):
        op.execute(f"alter table {table} enable row level security")
        op.execute(f"alter table {table} force row level security")
    op.execute(
        f"create policy business_isolation on memberships using (business_id = {TENANT} or user_id = {USER}) "
        f"with check (business_id = {TENANT})"
    )
    op.execute(
        f"""create policy business_isolation on role_grants
            using (business_id = {TENANT} or exists (
                   select 1 from memberships m where m.id = role_grants.membership_id and m.user_id = {USER}))
            with check (business_id = {TENANT})"""
    )
    op.execute(f"create policy business_isolation on invitations using (business_id = {TENANT}) with check (business_id = {TENANT})")
    op.execute("drop policy business_isolation on businesses")
    op.execute(
        f"""create policy business_isolation on businesses
            using (id = {TENANT} or exists (
                   select 1 from memberships m where m.business_id = businesses.id and m.user_id = {USER}
                      and m.status = 'active'))
            with check (id = {TENANT})"""
    )
    # First login looks up pending invitations by email before any business is selected.
    op.execute(
        """
        create function pending_invitations_for_email(p_email text)
        returns table (id uuid, business_id uuid, roles varchar[], display_name text)
        language sql stable security definer set search_path = public as $$
          select i.id, i.business_id, i.roles, i.display_name::text from invitations i
           where lower(i.email) = lower(p_email) and i.status = 'pending' and i.expires_at > now()
        $$
        """
    )
    op.execute("revoke all on function pending_invitations_for_email(text) from public")
    op.execute("grant execute on function pending_invitations_for_email(text) to smb_app")


def downgrade() -> None:
    op.execute("drop function if exists pending_invitations_for_email(text)")
    op.execute("drop policy business_isolation on businesses")
    op.execute(f"create policy business_isolation on businesses using (id = {TENANT}) with check (id = {TENANT})")
    for table in ("invitations", "role_grants", "memberships"):
        disable_business_rls(table)
        op.drop_table(table)
    for table in ("oidc_login_states", "sessions", "users"):
        op.drop_table(table)
