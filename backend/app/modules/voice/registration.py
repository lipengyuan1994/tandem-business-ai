from app.platform.registry import Registry

from . import api, lifecycle


def register(reg: Registry) -> None:
    reg.add_router(api.router, "/voice", ["voice"])
    reg.add_export_handler("voice", lifecycle.export)
    reg.add_erasure_handler("voice", lifecycle.erase)
    reg.retention_handlers["voice"] = lifecycle.cleanup
