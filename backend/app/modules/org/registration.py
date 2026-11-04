"""Registers the org module with the platform registry."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from app.platform.db import get_engine
from app.platform.registry import Registry

from . import api, lifecycle
from . import permissions as P


def resolve_public_business(slug: str) -> UUID | None:
    """slug -> business id through the security-definer function (works without a tenant context)."""
    with get_engine().connect() as conn:
        row = conn.execute(text("select id from public_business_by_slug(:slug)"), {"slug": slug}).first()
        return row[0] if row else None


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/org", ["org"])
    reg.add_router(api.public_router, "/public", ["public"])
    reg.set_public_business_resolver(resolve_public_business)
    reg.add_export_handler("org", lifecycle.export)
