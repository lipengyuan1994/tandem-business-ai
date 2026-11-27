from app.platform.registry import Registry

from . import api, events, lifecycle
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/payments", ["payments"])
    reg.add_router(api.relay_router, "/payments/relay", ["payments-relay"])
    reg.add_job(events.JOB_KIND, events.apply_job, transactional=False)
    reg.add_export_handler("payments", lifecycle.export)
