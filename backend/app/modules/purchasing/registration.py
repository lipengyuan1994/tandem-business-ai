from app.platform.registry import Registry

from . import api, api_public_quotes, api_quotes, executors, lifecycle, quote_executors, quote_service
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/purchasing", ["purchasing"])
    reg.add_router(api_quotes.router, "/purchasing", ["purchasing"])
    reg.add_router(api_public_quotes.router, "/public/quotes", ["public-quotes"])
    for spec in quote_executors.ALL:
        reg.add_executor(spec)
    reg.add_job("purchasing.expire_quote_invites", quote_service.expire_invites)
    reg.add_cron("purchasing-expire-quote-invites", "purchasing.expire_quote_invites", 86400, per_business=True)
    reg.add_erasure_handler("purchasing", lifecycle.erase)
    reg.add_erasure_links("purchasing", lifecycle.links)
    reg.add_executor(executors.CREATE_FROM_DRAFT)
    reg.add_executor(executors.APPROVE_PO)
    reg.add_export_handler("purchasing", lifecycle.export)
