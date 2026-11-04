"""Server-side sessions (06-security-and-privacy.md#authentication-flow-keycloak-backend-for-frontend).

The browser holds only a random token in an HttpOnly cookie; the database stores SHA-256 hashes of the token and
of the CSRF token. Permissions are recomputed on every request, so revoking a role takes effect immediately.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.config import get_settings, is_regulated
from app.platform.context import Context
from app.platform.db import SET_TENANT, transaction
from app.platform.errors import Unauthenticated
from app.platform.ids import new_id
from app.platform.permissions import hash_token
from app.platform.registry import SessionInfo

from . import repository
from .models import UserSession
from .roles import permissions_for

TOUCH_EVERY = timedelta(seconds=60)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_for(session_id: uuid.UUID) -> str:
    """Stable per-session CSRF token: HMAC(server secret, session id). Unguessable without the secret."""
    key = get_settings().app_secret_key.get_secret_value().encode()
    return hmac.new(key, b"csrf:" + session_id.bytes, hashlib.sha256).hexdigest()


def create_session(
    db: Session,
    user_id: uuid.UUID,
    business_id: uuid.UUID | None,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    authenticated_at: datetime | None = None,
) -> tuple[UserSession, str, str]:
    """Insert a session; returns (row, cookie token, csrf token). Raw tokens are never stored."""
    settings = get_settings()
    now = clock.now()
    auth_time = authenticated_at or now
    expires = auth_time + timedelta(hours=settings.staff_session_hours)
    if is_regulated():
        expires = min(expires, auth_time + timedelta(hours=8))
    session_id = new_id()
    token, csrf = new_token(), csrf_for(session_id)
    row = UserSession(
        id=session_id,
        token_hash=hash_token(token),
        user_id=user_id,
        active_business_id=business_id,
        csrf_token_hash=hash_token(csrf),
        created_at=now,
        authenticated_at=auth_time,
        last_seen_at=now,
        expires_at=expires,
        ip_hash=hash_token(ip + settings.app_secret_key.get_secret_value()) if ip else None,
        user_agent=(user_agent or "")[:300] or None,
    )
    db.add(row)
    db.flush()
    return row, token, csrf


def _anonymous_ctx() -> Context:
    return Context(request_id="session-resolve", actor_kind="system", user_id=None, business_id=None)


def is_live(row: UserSession) -> bool:
    now = clock.now()
    idle = timedelta(minutes=get_settings().staff_idle_minutes)
    absolute = row.authenticated_at + timedelta(hours=8) if is_regulated() else row.expires_at
    return row.revoked_at is None and min(row.expires_at, absolute) > now and row.last_seen_at + idle > now


def resolve(token: str) -> SessionInfo | None:
    """Session resolver registered with platform.permissions (cookie token → SessionInfo)."""
    with transaction(_anonymous_ctx()) as db:
        row = repository.get_session_by_hash(db, hash_token(token))
        if row is None or not is_live(row):
            return None
        user = repository.get_user(db, row.user_id)
        if user is None or user.status != "active":
            return None
        db.execute(SET_TENANT, {"b": str(row.active_business_id or ""), "u": str(row.user_id)})
        business_id = row.active_business_id
        permissions: frozenset[str] = frozenset()
        if business_id is not None:
            roles = repository.active_roles(db, row.user_id, business_id)
            if roles:
                permissions = permissions_for(roles)
            else:
                business_id = None  # membership suspended/removed since login: user must pick another business
        if clock.now() - row.last_seen_at > TOUCH_EVERY:
            repository.touch_session(db, row.id, clock.now())
        return SessionInfo(
            session_id=row.id,
            user_id=row.user_id,
            business_id=business_id,
            permissions=permissions,
            csrf_token_hash=row.csrf_token_hash,
        )


def rotate(db: Session, ctx: Context, business_id: uuid.UUID | None) -> tuple[str, str]:
    """Revoke the current session and issue a new one (login, business switch)."""
    assert ctx.user_id is not None
    previous = repository.session_for_rotation(db, ctx.user_id, ctx.session_id) if ctx.session_id is not None else None
    if is_regulated() and (previous is None or not is_live(previous)):
        raise Unauthenticated("auth.session_expired")
    authenticated_at = previous.authenticated_at if previous is not None else None
    if ctx.session_id is not None:
        repository.revoke_sessions(db, user_id=ctx.user_id, session_id=ctx.session_id, now=clock.now())
    replacement, token, csrf = create_session(db, ctx.user_id, business_id, authenticated_at=authenticated_at)
    if previous is not None:
        replacement.expires_at = min(replacement.expires_at, previous.expires_at)
    return token, csrf
