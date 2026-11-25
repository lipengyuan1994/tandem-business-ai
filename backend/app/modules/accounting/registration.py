from app.platform.registry import Registry

from . import api, lifecycle, sync
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/accounting", ["accounting"])
    reg.add_job(sync.JOB_KIND, sync.run_job, transactional=False)
    reg.add_cron("accounting-sync", sync.JOB_KIND, 900, per_business=True)
    reg.add_export_handler("accounting", lifecycle.export)
    reg.add_erasure_handler("accounting", lifecycle.erase)
