"""Public API of the identity module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.errors import NotFound

from . import repository
from .roles import POLICY_VERSION, ROLES, permissions_for
from .schemas import UserBrief


def get_user_brief(db: Session, ctx: Context, user_id: uuid.UUID) -> UserBrief:
    user = repository.get_user(db, user_id)
    if user is None:
        raise NotFound("identity.user_not_found")
    return UserBrief(id=user.id, display_name=user.display_name)


def ensure_user(db: Session, email: str, display_name: str) -> uuid.UUID:
    """Admin tooling (seed, import): find a user by email or create one with a claimable `pending:` identity."""
    from app.platform.ids import new_id

    from .login import PENDING_PREFIX
    from .models import User

    user = repository.get_user_by_email(db, email)
    if user is not None:
        return user.id
    user = User(
        id=new_id(), oidc_subject=f"{PENDING_PREFIX}{email.lower()}", email=email.lower(), display_name=display_name
    )
    db.add(user)
    db.flush()
    return user.id


def ensure_membership(
    db: Session, ctx: Context, user_id: uuid.UUID, roles: list[str], title: str | None = None
) -> uuid.UUID:
    """Admin tooling: active membership with exactly these roles in ctx.business_id (idempotent)."""
    from .models import Membership, RoleGrant

    assert ctx.business_id is not None
    membership = repository.get_membership_for_user(db, ctx, user_id)
    if membership is None:
        from app.platform.ids import new_id

        membership = Membership(id=new_id(), business_id=ctx.business_id, user_id=user_id, status="active", title=title)
        db.add(membership)
        db.flush()
    for role in set(roles) - repository.membership_roles(db, ctx, membership.id):
        db.add(RoleGrant(business_id=ctx.business_id, membership_id=membership.id, role=role))
    db.flush()
    return membership.id


def get_user_briefs(db: Session, ctx: Context, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, UserBrief]:
    """Batch lookup for display names (audit log, approvals)."""
    return {u.id: UserBrief(id=u.id, display_name=u.display_name) for u in repository.get_users(db, user_ids)}


def effective_permissions(db: Session, user_id: uuid.UUID, business_id: uuid.UUID) -> frozenset[str]:
    """Permissions right now. The caller's transaction must be for `business_id` (RLS)."""
    return permissions_for(repository.active_roles(db, user_id, business_id))


def active_roles(db: Session, user_id: uuid.UUID, business_id: uuid.UUID) -> set[str]:
    """Roles held right now through an active membership."""
    return repository.active_roles(db, user_id, business_id)


def has_permission_now(db: Session, user_id: uuid.UUID, business_id: uuid.UUID, permission: str) -> bool:
    return permission in effective_permissions(db, user_id, business_id)


def list_users_with_permission(db: Session, ctx: Context, permission: str) -> list[UserBrief]:
    roles = {role for role, perms in ROLES.items() if permission in perms}
    return [UserBrief(id=u.id, display_name=u.display_name) for u in repository.users_with_any_role(db, ctx, roles)]


__all__ = [
    "POLICY_VERSION",
    "ROLES",
    "UserBrief",
    "active_roles",
    "effective_permissions",
    "ensure_membership",
    "ensure_user",
    "get_user_brief",
    "get_user_briefs",
    "has_permission_now",
    "list_users_with_permission",
]


def dictation_names(
    db: Session, ctx: Context, entity_ids: set[uuid.UUID], limit: int = 40, *, only_selected: bool = False
) -> list[str]:
    """Only active members of the current business; no emails or global user lookup."""
    from sqlalchemy import select, true

    from .models import Membership, User

    ctx.require("identity.membership.read")
    return list(
        db.scalars(
            select(User.display_name)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.business_id == ctx.business_id,
                Membership.status == "active",
                User.status == "active",
                User.id.in_(entity_ids) if only_selected else true(),
            )
            .order_by(User.id.in_(entity_ids).desc(), Membership.updated_at.desc(), User.id)
            .limit(min(limit, 50))
        )
    )
