from app.platform.registry import Registry

from . import api, api_pay, events, lifecycle, paylinks
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/payments", ["payments"])
    reg.add_router(api.relay_router, "/payments/relay", ["payments-relay"])
    reg.add_router(api_pay.router, "/payments/pay", ["payments-pay"])
    reg.add_job(events.JOB_KIND, events.apply_job, transactional=False)
    reg.add_job(paylinks.CLOSE_JOB, paylinks.close_job, transactional=False)
    reg.add_job(paylinks.CHECK_JOB, paylinks.check_job, transactional=False)
    reg.add_cron("payments.check_requests", paylinks.CHECK_JOB, 3600, per_business=True)
    reg.subscribe("sales.document_changed", "payments.close-stale-checkouts", paylinks.on_document_changed)
    reg.add_export_handler("payments", lifecycle.export)
