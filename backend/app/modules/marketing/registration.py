from __future__ import annotations

from app.platform.registry import Registry

from . import api, executors, lifecycle, service
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_export_handler("marketing", lifecycle.export)
    reg.add_erasure_handler("marketing", lifecycle.erase)
    reg.add_restriction_handler("marketing", lifecycle.restrict)
    reg.add_router(api.router, "/marketing", ["marketing"])
    reg.add_router(api.public_router, "/public", ["public"])
    reg.add_executor(executors.APPROVE)
    reg.subscribe("approvals.proposal_closed", "marketing.on_proposal_closed", service.on_proposal_closed)
