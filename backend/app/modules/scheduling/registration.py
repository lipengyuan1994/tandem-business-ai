from __future__ import annotations

from app.platform.registry import Registry

from . import api, executors, lifecycle, service
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/scheduling", ["scheduling"])
    reg.add_router(api.public_router, "/public", ["public"])
    reg.add_export_handler("scheduling", lifecycle.export)
    reg.add_erasure_handler("scheduling", lifecycle.erase)
    reg.add_erasure_check("scheduling", lifecycle.check)
    reg.add_erasure_links("scheduling", lifecycle.links)
    for spec in (executors.BOOK, executors.RESCHEDULE, executors.CANCEL):
        reg.add_executor(spec)
    reg.add_job("scheduling.release_holds_scan", service.job_release_holds_scan)
    reg.add_job("scheduling.release_expired_holds", service.job_release_expired_holds)
    reg.add_cron("scheduling-release-holds", "scheduling.release_holds_scan", 60)
    reg.subscribe("approvals.proposal_closed", "scheduling.on_proposal_closed", service.on_proposal_closed)
