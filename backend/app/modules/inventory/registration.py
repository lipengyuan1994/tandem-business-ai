from __future__ import annotations

from app.platform.registry import Registry

from . import api, executors, lifecycle
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_export_handler("inventory", lifecycle.export)
    reg.add_erasure_handler("inventory", lifecycle.erase)
    reg.add_router(api.router, "/inventory", ["inventory"])
    reg.add_executor(executors.ADJUST)
    reg.add_executor(executors.DRAFT)
