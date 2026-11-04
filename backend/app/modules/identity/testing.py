"""Test helpers for modules that need real logged-in users (replaces platform's fake resolver)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.ids import new_id
from app.platform.permissions import SESSION_COOKIE

from .models import Membership, RoleGrant, User
from .roles import permissions_for
from .sessions import create_session


def make_user(email: str | None = None, name: str = "Test User") -> uuid.UUID:
    user_id = new_id()
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=None)
    with transaction(ctx) as db:
        db.add(
            User(
                id=user_id,
                oidc_subject=f"sub-{user_id}",
                email=email or f"user-{user_id.hex[-12:]}@example.com",
                display_name=name,
            )
        )
    return user_id


def add_member(user_id: uuid.UUID, business_id: uuid.UUID, roles: list[str], status: str = "active") -> uuid.UUID:
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business_id)
    membership_id = new_id()
    with transaction(ctx) as db:
        db.add(Membership(id=membership_id, business_id=business_id, user_id=user_id, status=status))
        db.flush()
        for role in roles:
            db.add(RoleGrant(business_id=business_id, membership_id=membership_id, role=role))
    return membership_id


def login_as(
    app: Any, business_id: uuid.UUID | None, roles: list[str], *, user_id: uuid.UUID | None = None, csrf: bool = True
) -> TestClient:
    """TestClient with a real session row for a (new or given) user holding `roles` in `business_id`."""
    if user_id is None:
        user_id = make_user()
        if business_id is not None:
            add_member(user_id, business_id, roles)
    with transaction(Context(request_id="t", actor_kind="system", user_id=user_id, business_id=business_id)) as db:
        _, token, csrf_token = create_session(db, user_id, business_id)
    client = TestClient(app, base_url="https://testserver")
    # Same domain httpx uses for cookies the server sets, so a rotated session replaces this one.
    client.cookies.set(SESSION_COOKIE, token, domain="testserver.local")
    if csrf:
        client.headers["X-CSRF-Token"] = csrf_token
    client.user_id = user_id
    client.permissions = permissions_for(set(roles))
    return client
