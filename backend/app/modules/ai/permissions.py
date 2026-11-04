"""Permissions of the ai module (docs/roadmap/ai/contracts.md §3). Granted to roles by identity/roles.py."""

from __future__ import annotations

ASSISTANT_USE = "ai.assistant.use"
SETTINGS_READ = "ai.settings.read"
SETTINGS_WRITE = "ai.settings.write"
ALL = frozenset({ASSISTANT_USE, SETTINGS_READ, SETTINGS_WRITE})

# What a background (system-triggered) assistant run may do. Write = hold only (scheduling).
AI_SYSTEM_PERMISSIONS: frozenset[str] = frozenset({
    "ai.assistant.use", "crm.customer.read", "conversations.thread.read", "catalog.item.read",
    "scheduling.appointment.read", "scheduling.resource.read", "scheduling.appointment.write",  # write = hold only
    "inventory.stock.read", "inventory.reorder.read", "purchasing.po.read", "sales.document.read",
    "org.business.read",
})  # fmt: skip
AI_TOOL_PERMISSIONS = AI_SYSTEM_PERMISSIONS  # upper bound for staff-triggered runs
USE_CASE_EXTRA_PERMISSIONS: dict[str, frozenset[str]] = {
    "campaign_draft": frozenset({"marketing.campaign.read", "marketing.campaign.write"}),
    "payment_followup": frozenset({"sales.document.write"}),  # owner decision 2026-11-04; proposals only
}
# Proposals created with an assistant context are checked against "ai.assistant.use"
# (approvals/service.py `_can_create`), not "approvals.proposal.create".
