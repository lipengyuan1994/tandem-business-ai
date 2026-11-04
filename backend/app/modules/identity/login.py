"""Turning a validated Keycloak identity into an application session."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import structlog
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.platform import audit, clock
from app.platform.context import Context
from app.platform.db import SET_TENANT
from app.platform.ids import new_id
from app.platform.permissions import hash_token

from . import repository
from .models import Invitation, Membership, OidcLoginState, RoleGrant, User
from .oidc import LoginFailed

log = structlog.get_logger(__name__)
STATE_TTL = timedelta(minutes=10)
PENDING_PREFIX = "pending:"


def safe_return_to(value: str | None) -> str:
    """Only same-site relative paths (open-redirect protection)."""
    if not value:
        return "/today"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/today"
    return value


def save_state(db: Session, *, state: str, nonce: str, code_verifier: str, return_to: str) -> None:
    db.add(
        OidcLoginState(
            state_hash=hash_token(state),
            nonce=nonce,
            code_verifier=code_verifier,
            return_to=safe_return_to(return_to),
            expires_at=clock.now() + STATE_TTL,
        )
    )


def take_state(db: Session, state: str) -> OidcLoginState:
    row = repository.pop_login_state(db, hash_token(state))
    if row is None or row.expires_at < clock.now():
        raise LoginFailed("unknown or expired state")
    return row


def _upsert_user(db: Session, claims: dict[str, Any]) -> User:
    subject, email = str(claims["sub"]), str(claims["email"]).lower()
    name = str(claims.get("name") or claims.get("preferred_username") or email)
    user = repository.get_user_by_subject(db, subject)
    if user is None:
        existing = repository.get_user_by_email(db, email)
        if existing is not None and existing.oidc_subject.startswith(PENDING_PREFIX):
            # Created by seed/import before the person signed in: the first Keycloak login with this email claims it.
            # Safe because Keycloak accounts are created only by administrators (self-registration is disabled).
            existing.oidc_subject = subject
            user = existing
    if user is None:
        if repository.get_user_by_email(db, email) is not None:
            # Same email, different identity-provider subject: refuse rather than silently merge accounts.
            raise LoginFailed("email already linked to another identity")
        user = User(id=new_id(), oidc_subject=subject, email=email, display_name=name)
        db.add(user)
        db.flush()
    if user.status != "active":
        raise LoginFailed("user disabled")
    user.last_login_at = clock.now()
    return user


def _accept_invitations(db: Session, user: User) -> None:
    for inv in repository.pending_invitations_for_email(db, user.email):
        business_id: uuid.UUID = inv["business_id"]
        db.execute(SET_TENANT, {"b": str(business_id), "u": str(user.id)})
        ctx = Context(request_id="login", actor_kind="user", user_id=user.id, business_id=business_id)
        membership = repository.get_membership_for_user(db, ctx, user.id)
        if membership is None:
            membership = Membership(id=new_id(), business_id=business_id, user_id=user.id, status="active")
            db.add(membership)
        else:
            membership.status = "active"
        db.flush()
        for role in inv["roles"]:
            if role not in repository.membership_roles(db, ctx, membership.id):
                db.add(RoleGrant(business_id=business_id, membership_id=membership.id, role=role))
        db.execute(
            update(Invitation).where(Invitation.id == inv["id"]).values(status="accepted", accepted_at=clock.now())
        )
        audit.record(
            db,
            ctx,
            action="identity.invitation.accepted",
            target_type="membership",
            target_id=membership.id,
            summary=f"{user.display_name} joined with roles {', '.join(inv['roles'])}",
        )
    db.execute(SET_TENANT, {"b": "", "u": str(user.id)})


def complete_login(db: Session, claims: dict[str, Any]) -> tuple[User, uuid.UUID | None]:
    """Upsert the user, accept pending invitations, and choose the active business (if exactly one)."""
    user = _upsert_user(db, claims)
    db.execute(SET_TENANT, {"b": "", "u": str(user.id)})
    _accept_invitations(db, user)
    memberships = repository.memberships_of_user(db, user.id)
    business_id = memberships[0]["business_id"] if len(memberships) == 1 else None
    log.info("login_completed", user_id=str(user.id), memberships=len(memberships))
    return user, business_id
