"""All SQL for identity. Global tables (users, sessions, login states) are read by id/hash only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.platform.context import Context

from .models import Invitation, Membership, OidcLoginState, RoleGrant, User, UserSession


# ------------------------------------------------------------------------------------------------- users
def get_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def get_users(db: Session, user_ids: set[uuid.UUID]) -> list[User]:
    return list(db.scalars(select(User).where(User.id.in_(user_ids)))) if user_ids else []


def get_user_by_subject(db: Session, subject: str) -> User | None:
    return db.scalar(select(User).where(User.oidc_subject == subject))


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.lower()))


# ---------------------------------------------------------------------------------------------- sessions
def get_session_by_hash(db: Session, token_hash: str) -> UserSession | None:
    return db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))


def session_for_rotation(db: Session, user_id: uuid.UUID, session_id: uuid.UUID) -> UserSession | None:
    return db.scalar(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.id == session_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def touch_session(db: Session, session_id: uuid.UUID, now: datetime) -> None:
    db.execute(update(UserSession).where(UserSession.id == session_id).values(last_seen_at=now))


def revoke_sessions(db: Session, *, user_id: uuid.UUID, session_id: uuid.UUID | None, now: datetime) -> int:
    stmt = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if session_id is not None:
        stmt = stmt.where(UserSession.id == session_id)
    return db.execute(stmt.values(revoked_at=now)).rowcount  # type: ignore[attr-defined, no-any-return]


def pop_login_state(db: Session, state_hash: str) -> OidcLoginState | None:
    state = db.get(OidcLoginState, state_hash)
    if state is not None:
        db.delete(state)
        db.flush()
    return state


# ------------------------------------------------------------------------------------ memberships, roles
def lock_owner_changes(db: Session, ctx: Context) -> None:
    """Serialize membership/role changes before reading the active-owner invariant."""
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"identity:owners:{ctx.business_id}"},
    )


def active_roles(db: Session, user_id: uuid.UUID, business_id: uuid.UUID) -> set[str]:
    """Roles of an *active* membership. Caller's transaction must set app.user_id (RLS admits own rows)."""
    stmt = (
        select(RoleGrant.role)
        .join(
            Membership, (Membership.id == RoleGrant.membership_id) & (Membership.business_id == RoleGrant.business_id)
        )
        .where(
            Membership.user_id == user_id,
            Membership.business_id == business_id,
            Membership.status == "active",
            RoleGrant.revoked_at.is_(None),
        )
    )
    return set(db.scalars(stmt))


def memberships_of_user(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """select m.business_id, b.name, b.slug, b.timezone,
                      coalesce(array_agg(g.role order by g.role) filter (where g.role is not null), '{}') as roles
                 from memberships m join businesses b on b.id = m.business_id
                 left join role_grants g on g.membership_id = m.id and g.revoked_at is null
                where m.user_id = :u and m.status = 'active' and b.status = 'active'
                group by m.business_id, b.name, b.slug, b.timezone order by b.name"""
        ),
        {"u": user_id},
    ).mappings()
    return [dict(r) for r in rows]


def get_membership(db: Session, ctx: Context, membership_id: uuid.UUID) -> Membership | None:
    return db.scalar(
        select(Membership).where(Membership.business_id == ctx.business_id, Membership.id == membership_id)
    )


def get_membership_for_user(db: Session, ctx: Context, user_id: uuid.UUID) -> Membership | None:
    return db.scalar(select(Membership).where(Membership.business_id == ctx.business_id, Membership.user_id == user_id))


def list_members(db: Session, ctx: Context) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """select m.id as membership_id, m.user_id, u.display_name, u.email, m.title, m.status, m.version,
                      coalesce(array_agg(g.role order by g.role) filter (where g.role is not null), '{}') as roles
                 from memberships m join users u on u.id = m.user_id
                 left join role_grants g on g.membership_id = m.id and g.revoked_at is null
                where m.business_id = :b and m.status <> 'removed'
                group by m.id, u.display_name, u.email order by u.display_name"""
        ),
        {"b": ctx.business_id},
    ).mappings()
    return [dict(r) for r in rows]


def active_members_identity(db: Session, ctx: Context) -> list[dict[str, Any]]:
    """Active members of ctx's business with the identity their user row is bound to (admin tooling: demo logins)."""
    rows = db.execute(
        text(
            """select lower(u.email) as email, u.display_name, u.oidc_subject, u.status as user_status
                 from memberships m join users u on u.id = m.user_id
                where m.business_id = :b and m.status = 'active'"""
        ),
        {"b": ctx.business_id},
    ).mappings()
    return [dict(r) for r in rows]


def membership_roles(db: Session, ctx: Context, membership_id: uuid.UUID) -> set[str]:
    stmt = select(RoleGrant.role).where(
        RoleGrant.business_id == ctx.business_id,
        RoleGrant.membership_id == membership_id,
        RoleGrant.revoked_at.is_(None),
    )
    return set(db.scalars(stmt))


def count_active_owners(db: Session, ctx: Context) -> int:
    stmt = (
        select(func.count())
        .select_from(RoleGrant)
        .join(Membership, Membership.id == RoleGrant.membership_id)
        .where(
            RoleGrant.business_id == ctx.business_id,
            RoleGrant.role == "owner",
            RoleGrant.revoked_at.is_(None),
            Membership.status == "active",
        )
    )
    return int(db.scalar(stmt) or 0)


def revoke_role(db: Session, ctx: Context, membership_id: uuid.UUID, role: str, now: datetime) -> None:
    db.execute(
        update(RoleGrant)
        .where(
            RoleGrant.business_id == ctx.business_id,
            RoleGrant.membership_id == membership_id,
            RoleGrant.role == role,
            RoleGrant.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


def users_with_any_role(db: Session, ctx: Context, roles: set[str]) -> list[User]:
    stmt = (
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .join(RoleGrant, RoleGrant.membership_id == Membership.id)
        .where(
            Membership.business_id == ctx.business_id,
            Membership.status == "active",
            RoleGrant.revoked_at.is_(None),
            RoleGrant.role.in_(roles),
            User.status == "active",
        )
        .distinct()
    )
    return list(db.scalars(stmt))


# ---------------------------------------------------------------------------------------- invitations
def pending_invitations_for_email(db: Session, email: str) -> list[dict[str, Any]]:
    rows = db.execute(text("select * from pending_invitations_for_email(:e)"), {"e": email}).mappings()
    return [dict(r) for r in rows]


def get_invitation(db: Session, ctx: Context, invitation_id: uuid.UUID) -> Invitation | None:
    return db.scalar(
        select(Invitation).where(Invitation.business_id == ctx.business_id, Invitation.id == invitation_id)
    )


def pending_invitation_exists(db: Session, ctx: Context, email: str) -> bool:
    stmt = select(Invitation.id).where(
        Invitation.business_id == ctx.business_id,
        func.lower(Invitation.email) == email.lower(),
        Invitation.status == "pending",
    )
    return db.scalar(stmt) is not None
