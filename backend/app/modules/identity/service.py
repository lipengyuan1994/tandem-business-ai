"""Staff, roles and invitations (M01 rules 2-4, 8)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform import audit, clock
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import SET_TENANT
from app.platform.errors import Conflict, Forbidden, NotFound
from app.platform.ids import new_id

from . import keycloak_admin, repository
from . import permissions as P
from .models import Invitation, Membership, RoleGrant, User
from .schemas import BootstrapResult, InvitationIn, InvitationOut, MemberUpdate

INVITATION_TTL = timedelta(days=7)


def _require_owner_power(ctx: Context, roles_touched: set[str]) -> None:
    if "owner" in roles_touched and not ctx.has(P.OWNER_GRANT):
        raise Forbidden("identity.owner_grant_forbidden")


def _membership(db: Session, ctx: Context, membership_id: uuid.UUID) -> Membership:
    membership = repository.get_membership(db, ctx, membership_id)
    if membership is None or membership.status == "removed":
        raise NotFound("identity.membership_not_found")
    return membership


def list_members(db: Session, ctx: Context) -> list[dict[str, Any]]:
    ctx.require(P.MEMBERSHIP_READ)
    return repository.list_members(db, ctx)


def update_member(db: Session, ctx: Context, membership_id: uuid.UUID, data: MemberUpdate) -> Membership:
    ctx.require(P.MEMBERSHIP_WRITE)
    repository.lock_owner_changes(db, ctx)
    membership = _membership(db, ctx, membership_id)
    if membership.version != data.version:
        raise Conflict("identity.membership_version_conflict", current_version=membership.version)
    roles = repository.membership_roles(db, ctx, membership_id)
    if data.status and data.status != membership.status:
        _require_owner_power(ctx, roles)
    if data.status and data.status != "active":
        _require_owner_power(ctx, roles)
        if "owner" in roles and repository.count_active_owners(db, ctx) <= 1:
            raise Conflict("identity.last_owner")
    before = {"title": membership.title, "status": membership.status}
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    for key, value in changes.items():
        setattr(membership, key, value)
    membership.version += 1
    db.flush()
    audit.record(
        db,
        ctx,
        action="identity.membership.updated",
        target_type="membership",
        target_id=membership_id,
        summary="Staff membership updated",
        changes=audit.diff(before, membership, ["title", "status"]),
    )
    return membership


def set_roles(db: Session, ctx: Context, membership_id: uuid.UUID, new_roles: set[str]) -> set[str]:
    ctx.require(P.MEMBERSHIP_WRITE)
    repository.lock_owner_changes(db, ctx)
    membership = _membership(db, ctx, membership_id)
    current = repository.membership_roles(db, ctx, membership_id)
    added, removed = new_roles - current, current - new_roles
    _require_owner_power(ctx, added | removed)
    if "owner" in removed and membership.status == "active" and repository.count_active_owners(db, ctx) <= 1:
        raise Conflict("identity.last_owner")
    now = clock.now()
    for role in removed:
        repository.revoke_role(db, ctx, membership_id, role, now)
    assert ctx.business_id is not None
    for role in added:
        db.add(RoleGrant(business_id=ctx.business_id, membership_id=membership_id, role=role, granted_by=ctx.user_id))
    db.flush()
    if added or removed:
        audit.record(
            db,
            ctx,
            action="identity.roles.changed",
            target_type="membership",
            target_id=membership_id,
            summary=f"Roles changed: +{sorted(added)} -{sorted(removed)}",
            changes={"roles": {"from": sorted(current), "to": sorted(new_roles)}},
        )
    return new_roles


def invite(db: Session, ctx: Context, data: InvitationIn) -> InvitationOut:
    ctx.require(P.MEMBERSHIP_WRITE)
    roles: set[str] = set(data.roles)
    _require_owner_power(ctx, roles)
    email = str(data.email).lower()
    if repository.pending_invitation_exists(db, ctx, email):
        raise Conflict("identity.invitation_exists")
    assert ctx.business_id is not None
    existing = repository.get_user_by_email(db, email)
    invitation = Invitation(
        id=new_id(),
        business_id=ctx.business_id,
        email=email,
        display_name=data.display_name,
        roles=sorted(roles),
        invited_by=ctx.user_id,
        expires_at=clock.now() + INVITATION_TTL,
        status="pending",
    )
    temp_password: str | None = None
    if existing is not None:
        # Already a user (e.g. member of another business): add the membership now.
        if repository.get_membership_for_user(db, ctx, existing.id) is not None:
            raise Conflict("identity.already_member")
        membership = Membership(id=new_id(), business_id=ctx.business_id, user_id=existing.id, status="active")
        db.add(membership)
        db.flush()
        for role in sorted(roles):
            db.add(
                RoleGrant(business_id=ctx.business_id, membership_id=membership.id, role=role, granted_by=ctx.user_id)
            )
        invitation.status, invitation.accepted_at = "accepted", clock.now()
    else:
        smtp = bool(get_settings().smtp_url)
        temp_password = None if smtp else keycloak_admin.temporary_password()
        invitation.keycloak_user_id = keycloak_admin.create_user(email, data.display_name, temp_password)
        if smtp:
            keycloak_admin.send_setup_email(invitation.keycloak_user_id)
    db.add(invitation)
    db.flush()
    audit.record(
        db,
        ctx,
        action="identity.invitation.created",
        target_type="invitation",
        target_id=invitation.id,
        summary=f"Invited {data.display_name} as {', '.join(sorted(roles))}",
    )
    return InvitationOut(
        id=invitation.id,
        email=email,
        display_name=data.display_name,
        roles=sorted(roles),
        status=invitation.status,
        expires_at=invitation.expires_at,
        temporary_password=temp_password,
    )


def revoke_invitation(db: Session, ctx: Context, invitation_id: uuid.UUID) -> None:
    ctx.require(P.MEMBERSHIP_WRITE)
    invitation = repository.get_invitation(db, ctx, invitation_id)
    if invitation is None:
        raise NotFound("identity.invitation_not_found")
    if invitation.status != "pending":
        raise Conflict("identity.invitation_not_pending")
    invitation.status = "revoked"
    audit.record(
        db,
        ctx,
        action="identity.invitation.revoked",
        target_type="invitation",
        target_id=invitation_id,
        summary="Invitation revoked",
    )


def bootstrap_business(
    db: Session, *, name: str, slug: str, timezone: str, currency: str, locale: str, owner_email: str, owner_name: str
) -> BootstrapResult:
    """CLI only: create a business, its owner's Keycloak account (temporary password) and the owner membership."""
    business_id = new_id()
    ctx = Context(request_id="bootstrap", actor_kind="system", user_id=None, business_id=business_id)
    db.execute(SET_TENANT, {"b": str(business_id), "u": ""})
    org.create_business(db, ctx, name=name, slug=slug, timezone=timezone, currency=currency, locale=locale)
    email = owner_email.lower()
    user = repository.get_user_by_email(db, email)
    password: str | None = None
    if user is None:
        subject = keycloak_admin.find_user_id(email)
        if subject is None:
            password = keycloak_admin.temporary_password()
            subject = keycloak_admin.create_user(email, owner_name, password)
        user = User(id=new_id(), oidc_subject=subject, email=email, display_name=owner_name)
        db.add(user)
        db.flush()
    membership = Membership(id=new_id(), business_id=business_id, user_id=user.id, status="active", title="Owner")
    db.add(membership)
    db.flush()
    db.add(RoleGrant(business_id=business_id, membership_id=membership.id, role="owner"))
    audit.record(
        db,
        ctx,
        action="identity.business.bootstrapped",
        target_type="membership",
        target_id=membership.id,
        summary=f"Owner {owner_name} added",
    )
    return BootstrapResult(
        business_id=business_id, owner_user_id=user.id, owner_email=email, temporary_password=password
    )


def dev_reset_account(db: Session, email: str) -> str:
    """Development and end-to-end tests only: give an existing staff user a fresh Keycloak account.

    Deletes any Keycloak user with this email, creates a new one with a temporary password (Keycloak forces a new
    password and TOTP setup at first login) and points the app user at it. Returns the temporary password.
    """
    if get_settings().app_env not in ("dev", "test"):
        raise PermissionError("dev_reset_account is disabled outside dev and test")
    user = repository.get_user_by_email(db, email.lower())
    if user is None:
        raise LookupError(f"no app user with email {email}; seed the demo data first")
    existing = keycloak_admin.find_user_id(user.email)
    if existing:
        keycloak_admin.delete_user(existing)
    password = keycloak_admin.temporary_password()
    user.oidc_subject = keycloak_admin.create_user(user.email, user.display_name, password)
    return password
