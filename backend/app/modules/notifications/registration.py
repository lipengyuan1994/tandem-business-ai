from __future__ import annotations

from app.platform.registry import Registry

from . import api, lifecycle, outbound, service
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.proposal_delivery_reader = outbound.delivery_state
    reg.add_router(api.router, "/notifications", ["notifications"])
    reg.add_export_handler("notifications", lifecycle.export)
    reg.add_erasure_handler("notifications", lifecycle.erase)
    reg.add_erasure_links("notifications", lifecycle.links)
    reg.add_restriction_handler("notifications", lifecycle.restrict)
    reg.add_job("notifications.deliver", service.job_deliver)
    reg.add_job("notifications.reap_sending", service.job_reap_sending)
    reg.add_cron("notifications-reap-sending", "notifications.reap_sending", 60)
    reg.subscribe("approvals.proposal_created", "notifications.on_proposal_created", service.on_proposal_created)
