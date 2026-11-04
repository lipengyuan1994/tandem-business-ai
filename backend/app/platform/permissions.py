"""Permission checks for HTTP routes.

`require("crm.customer.write")` is a FastAPI dependency factory: session cookie -> user -> active business ->
effective permissions -> CSRF check on unsafe methods -> Context. Session lookup is delegated to the resolver
registered by the identity module, so platform never imports identity.

Every route must depend on `require(...)`, `require_session()` or `public_context`, or be declared public
(tests/architecture/test_route_permissions.py).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import Any

from fastapi import Request

from app.platform.context import Context
from app.platform.errors import Forbidden, NotFound, Unauthenticated
from app.platform.registry import SessionInfo, get_registry

SESSION_COOKIE = "smb_session"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _resolve_session(request: Request) -> SessionInfo:
    token = request.cookies.get(SESSION_COOKIE)
    resolver = get_registry().session_resolver
    if not token or resolver is None:
        raise Unauthenticated("auth.unauthenticated")
    info = resolver(token)
    if info is None:
        raise Unauthenticated("auth.unauthenticated")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(hash_token(sent), info.csrf_token_hash):
            raise Forbidden("auth.csrf_failed")
    return info


def _context(request: Request, info: SessionInfo) -> Context:
    return Context(
        request_id=getattr(request.state, "request_id", "-"),
        actor_kind="user",
        user_id=info.user_id,
        business_id=info.business_id,
        permissions=info.permissions,
        session_id=info.session_id,
    )


def require(permission: str) -> Callable[[Request], Context]:
    """Dependency: authenticated user with an active business holding `permission`."""

    def dependency(request: Request) -> Context:
        info = _resolve_session(request)
        if info.business_id is None:
            raise Forbidden("auth.no_active_business")
        ctx = _context(request, info)
        ctx.require(permission)
        return ctx

    dependency.smb_permission = permission  # type: ignore[attr-defined]
    return dependency


def require_session() -> Callable[[Request], Context]:
    """Dependency: authenticated user; no business or permission required (e.g. /auth/me, business switch)."""

    def dependency(request: Request) -> Context:
        return _context(request, _resolve_session(request))

    dependency.smb_permission = "session"  # type: ignore[attr-defined]
    return dependency


def public_context(business_slug: str, request: Request) -> Context:
    """Dependency for /public/{business_slug}/... routes: visitor context scoped to one business."""
    resolver = get_registry().public_business_resolver
    business_id = resolver(business_slug) if resolver else None
    if business_id is None:
        raise NotFound("org.business_not_found")
    return Context(
        request_id=getattr(request.state, "request_id", "-"),
        actor_kind="visitor",
        user_id=None,
        business_id=business_id,
        permissions=frozenset({"public"}),
    )


public_context.smb_permission = "public"  # type: ignore[attr-defined]


def permission_of(call: Any) -> str | None:
    return getattr(call, "smb_permission", None)
