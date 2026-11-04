from __future__ import annotations

from app.platform.registry import Registry

from . import api, lifecycle
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_export_handler("catalog", lifecycle.export)
    reg.add_router(api.router, "/catalog", ["catalog"])
    reg.add_router(api.public_router, "/public", ["public"])
