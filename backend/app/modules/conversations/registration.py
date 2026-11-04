from __future__ import annotations

from app.platform.registry import Registry

from . import api, executors, lifecycle, service
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/conversations", ["conversations"])
    reg.add_router(api.public_router, "/public", ["public"])
    reg.add_executor(executors.SEND_REPLY)
    reg.add_export_handler("conversations", lifecycle.export)
    reg.add_erasure_handler("conversations", lifecycle.erase)
    reg.add_restriction_handler("conversations", lifecycle.restrict)
    reg.add_erasure_links("conversations", lifecycle.links)
    reg.subscribe(
        "notifications.delivery_status_changed", "conversations.on_delivery_status", service.on_delivery_status
    )
