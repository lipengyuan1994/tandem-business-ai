from __future__ import annotations

from app.platform.registry import Registry

from . import api, lifecycle
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/audit", ["audit"])
    reg.add_export_handler("audit", lifecycle.export)
    reg.add_erasure_handler("audit", lifecycle.erase)
