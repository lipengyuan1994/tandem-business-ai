from app.platform.registry import Registry

from . import api, executors, lifecycle
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/sales", ["sales"])
    reg.add_executor(executors.CREATE_QUOTE)
    reg.add_executor(executors.CONFIRM_ORDER)
    reg.add_executor(executors.SEND_PAYMENT_REMINDER)
    reg.add_export_handler("sales", lifecycle.export)
    reg.add_erasure_handler("sales", lifecycle.erase)
    reg.add_erasure_links("sales", lifecycle.links)
