from __future__ import annotations

from app.platform.registry import Registry

from . import api, executors, lifecycle
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/crm", ["crm"])
    reg.add_executor(executors.CREATE)
    reg.add_executor(executors.UPDATE)
    reg.add_export_handler("crm", lifecycle.export)
    reg.add_erasure_handler("crm", lifecycle.erase)
    reg.add_restriction_handler("crm", lifecycle.restrict)
