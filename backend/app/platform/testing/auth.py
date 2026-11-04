"""Test helpers for contexts and authenticated HTTP clients.

Until identity (P2-02) exists, `client_for` installs a fake session resolver into the registry.
After P2-02, identity's `login_as` creates real session rows; the fake stays for platform-only tests.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterable

from fastapi.testclient import TestClient

from app.platform.context import Context
from app.platform.ids import new_id
from app.platform.permissions import SESSION_COOKIE, hash_token
from app.platform.registry import Registry, SessionInfo


def ctx_for(
    business_id: uuid.UUID | None,
    permissions: Iterable[str] = (),
    *,
    user_id: uuid.UUID | None = None,
    actor: str = "user",
) -> Context:
    return Context(
        request_id="test-" + secrets.token_hex(4),
        actor_kind=actor,  # type: ignore[arg-type]
        user_id=user_id or (new_id() if actor == "user" else None),
        business_id=business_id,
        permissions=frozenset(permissions),
    )


class FakeSessions:
    """In-memory session resolver: token -> SessionInfo."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionInfo] = {}

    def __call__(self, token: str) -> SessionInfo | None:
        return self.sessions.get(token)

    def add(
        self, business_id: uuid.UUID | None, permissions: Iterable[str], user_id: uuid.UUID | None = None
    ) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.sessions[token] = SessionInfo(
            session_id=new_id(),
            user_id=user_id or new_id(),
            business_id=business_id,
            permissions=frozenset(permissions),
            csrf_token_hash=hash_token(csrf),
        )
        return token, csrf


def client_for(
    app: object, registry: Registry, business_id: uuid.UUID | None, permissions: Iterable[str], *, csrf: bool = True
) -> TestClient:
    """TestClient logged in through the fake resolver, sending the CSRF header unless csrf=False."""
    fake = registry.session_resolver if isinstance(registry.session_resolver, FakeSessions) else FakeSessions()
    registry.session_resolver = fake  # test-only mutation of a frozen registry's resolver
    token, csrf_token = fake.add(business_id, permissions)
    client = TestClient(app)  # type: ignore[arg-type]
    client.cookies.set(SESSION_COOKIE, token)
    if csrf:
        client.headers["X-CSRF-Token"] = csrf_token
    return client
