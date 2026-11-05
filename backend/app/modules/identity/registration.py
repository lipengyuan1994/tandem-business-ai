"""Registers identity: session resolver, permissions, routers."""

from __future__ import annotations

from app.platform.registry import Registry

from . import api_auth, api_members, lifecycle, sessions
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.set_session_resolver(sessions.resolve)
    reg.add_router(api_auth.router, "/auth", ["auth"])
    reg.add_router(api_members.router, "/identity", ["identity"])
    reg.add_export_handler("identity", lifecycle.export)
    reg.add_job("identity.retention", lifecycle.cleanup)
    reg.add_cron("identity-retention", "identity.retention", 86400)
    # Login and callback run before a session exists (protected by state/PKCE/nonce and rate limits).
    reg.add_public_route("/api/v1/auth/login")
    reg.add_public_route("/api/v1/auth/callback")
