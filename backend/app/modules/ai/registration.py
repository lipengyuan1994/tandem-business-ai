from __future__ import annotations

from app.platform.registry import Registry

from . import api, jobs, lifecycle, subscribers
from . import permissions as P
from .gateway.router import check_routes_at_startup


def register(reg: Registry) -> None:
    from . import chains, team_budget

    reg.ai_proposal_context = chains.proposal_context
    reg.ai_policy_enabled = chains.policy_enabled
    reg.ai_execution_context = chains.execution_context
    reg.ai_chain_stop = team_budget.stop_chain
    reg.ai_reserve_action = team_budget.reserve_action
    reg.add_permissions(P.ALL)
    reg.add_export_handler("ai", lifecycle.export)
    reg.add_erasure_handler("ai", lifecycle.erase)
    reg.add_router(api.router, "/ai", ["ai"])
    reg.subscribe("conversations.message_received", "ai.intake-trigger", subscribers.intake_trigger)
    reg.subscribe("conversations.handoff_requested", "ai.handoff-trigger", subscribers.handoff_trigger)
    reg.subscribe("conversations.ai_ownership_changed", "ai.ownership-trigger", subscribers.handoff_trigger)
    reg.add_job("ai.intake", jobs.guarded(jobs.intake_job, "front_desk"), transactional=False)
    reg.subscribe("inventory.stock_below_reorder_level", "ai.reorder-trigger", subscribers.reorder_trigger)
    reg.add_job("ai.reorder_note", jobs.guarded(jobs.reorder_note_job, "inventory"), transactional=False)
    reg.subscribe("purchasing.quote_received", "ai.compare-trigger", subscribers.compare_trigger)
    reg.add_job(
        "ai.procurement_request_quotes",
        jobs.guarded(jobs.procurement_request_quotes_job, "purchasing"),
        transactional=False,
    )
    reg.add_job("ai.procurement_compare", jobs.guarded(jobs.procurement_compare_job, "purchasing"), transactional=False)
    reg.add_job("ai.procurement_scan", jobs.guarded(jobs.procurement_scan_job, "purchasing"), transactional=False)
    reg.add_job(
        "ai.procurement_followup", jobs.guarded(jobs.procurement_followup_job, "purchasing"), transactional=False
    )
    reg.add_cron("ai-procurement-scan", "ai.procurement_scan", every_seconds=3600, per_business=True)
    reg.add_job("ai.assistant_retention", jobs.assistant_retention_job, transactional=False)
    reg.add_cron("ai-assistant-retention", "ai.assistant_retention", every_seconds=86400, per_business=True)
    reg.add_job("ai.payment_followup", jobs.guarded(jobs.payment_followup_job, "finance"), transactional=False)
    reg.add_job(
        "ai.payment_followup_scan", jobs.guarded(jobs.payment_followup_scan_job, "finance"), transactional=False
    )
    reg.add_cron("ai-payment-followup-scan", "ai.payment_followup_scan", every_seconds=86400, per_business=True)
    check_routes_at_startup()  # APP_ENV=prod refuses a route file with a `fake` provider
