from __future__ import annotations

import os

from app.platform.config import get_settings
from app.platform.registry import Registry

from . import api, engine, lifecycle, testing_executor
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/approvals", ["approvals"])
    reg.add_export_handler("approvals", lifecycle.export)
    reg.add_erasure_handler("approvals", lifecycle.erase)
    reg.add_erasure_check("approvals", lifecycle.check)
    reg.add_erasure_links("approvals", lifecycle.links)
    reg.add_job("approvals.execute", engine.job_execute)
    reg.add_job("approvals.expire_scan", engine.job_expire_scan)
    reg.add_job("approvals.expire_business", engine.job_expire_business)
    reg.add_cron("approvals-expire", "approvals.expire_scan", 60)
    env = get_settings().app_env
    if env == "test" or (env == "dev" and os.environ.get("DEV_TEST_EXECUTORS") == "1"):
        reg.add_executor(testing_executor.SPEC)
