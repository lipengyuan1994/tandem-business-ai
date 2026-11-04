"""Tables owned by identity (docs/roadmap/modules/M01-identity.md#tables)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id
from app.platform.models import Base, BusinessScopedBase, TimestampMixin, scoped_table_args


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    oidc_subject: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    last_login_at: Mapped[datetime | None]
    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        CheckConstraint("status in ('active','disabled')", name="status"),
    )


class UserSession(TimestampMixin, Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    active_business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    authenticated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = (Index("ix_sessions_user", "user_id"),)


class OidcLoginState(Base):
    __tablename__ = "oidc_login_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(128))
    code_verifier: Mapped[str] = mapped_column(String(128))
    return_to: Mapped[str] = mapped_column(String(500))
    expires_at: Mapped[datetime]


class Membership(BusinessScopedBase):
    __tablename__ = "memberships"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="active")
    title: Mapped[str | None] = mapped_column(String(120))
    __table_args__ = scoped_table_args(
        Index("uq_memberships_business_user", "business_id", "user_id", unique=True),
        CheckConstraint("status in ('invited','active','suspended','removed')", name="status"),
    )


class RoleGrant(BusinessScopedBase):
    __tablename__ = "role_grants"
    membership_id: Mapped[uuid.UUID]
    role: Mapped[str] = mapped_column(String(32))
    granted_by: Mapped[uuid.UUID | None]
    revoked_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "membership_id"], ["memberships.business_id", "memberships.id"]),
        Index(
            "uq_role_grants_active", "membership_id", "role", unique=True, postgresql_where=text("revoked_at is null")
        ),
        CheckConstraint(
            "role in ('owner','admin','operator','inventory_manager','marketing_manager','auditor')", name="role"
        ),
    )


class Invitation(BusinessScopedBase):
    __tablename__ = "invitations"
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    invited_by: Mapped[uuid.UUID | None]
    keycloak_user_id: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16), default="pending")
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = scoped_table_args(
        CheckConstraint("status in ('pending','accepted','revoked','expired')", name="status"),
        Index("ix_invitations_email", text("lower(email)")),
    )
