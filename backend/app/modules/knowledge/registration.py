from app.platform.registry import Registry

from . import api, lifecycle


def register(reg: Registry) -> None:
    reg.add_router(api.router, "/knowledge", ["knowledge"])
    reg.add_export_handler("knowledge", lifecycle.export)
    reg.add_erasure_handler("knowledge", lifecycle.erase)
    reg.add_job("knowledge.retention", lifecycle.cleanup)
    reg.add_cron("knowledge-retention", "knowledge.retention", 86400, per_business=True)
