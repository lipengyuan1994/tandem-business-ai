from app.platform.registry import Registry

from . import api, api_pay, disputes, drafts, events, lifecycle, paylinks, payouts, refunds
from . import permissions as P


def register(reg: Registry) -> None:
    reg.add_permissions(P.ALL)
    reg.add_router(api.router, "/payments", ["payments"])
    reg.add_router(api.relay_router, "/payments/relay", ["payments-relay"])
    reg.add_router(api_pay.router, "/payments/pay", ["payments-pay"])
    reg.add_job(events.JOB_KIND, events.apply_job, transactional=False)
    reg.add_job(refunds.REPLAY_JOB, events.replay_job)
    reg.add_job(paylinks.CLOSE_JOB, paylinks.close_job, transactional=False)
    reg.add_job(paylinks.CHECK_JOB, paylinks.check_job, transactional=False)
    reg.add_cron("payments.check_requests", paylinks.CHECK_JOB, 3600, per_business=True)
    reg.add_executor(refunds.SPEC)
    reg.add_job(refunds.SEND_JOB, refunds.send_job, transactional=False)
    reg.add_job(refunds.CHECK_JOB, disputes.check_job, transactional=False)
    reg.add_cron("payments.check_refunds", refunds.CHECK_JOB, 3600, per_business=True)
    reg.add_job(payouts.FEE_JOB, payouts.read_fees_job, transactional=False)
    reg.add_job(payouts.CHECK_JOB, payouts.check_job, transactional=False)
    reg.add_cron("payments.check_payouts", payouts.CHECK_JOB, 3600, per_business=True)
    reg.add_job(drafts.DRAFT_JOB, drafts.draft_surplus_job)
    reg.add_job(drafts.MATCH_JOB, drafts.match_job)
    reg.subscribe("sales.document_changed", "payments.match-dashboard-refunds", drafts.on_document_changed)
    reg.subscribe("approvals.proposal_closed", "payments.on_refund_proposal_closed", refunds.on_proposal_closed)
    reg.subscribe("sales.document_changed", "payments.close-stale-checkouts", paylinks.on_document_changed)
    reg.set_reminder_pay_link(paylinks.reminder_link)
    reg.add_export_handler("payments", lifecycle.export)
