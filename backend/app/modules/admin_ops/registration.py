from __future__ import annotations

from app.platform.registry import Registry

from . import api, service
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/admin", ["admin"])
    reg.add_export_handler("admin_ops", service.export_own)
