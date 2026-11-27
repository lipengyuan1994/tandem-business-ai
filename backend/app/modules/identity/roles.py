"""Role → permission bundles (06-security-and-privacy.md#permission-matrix). Changing a bundle bumps POLICY_VERSION.

Permission strings for modules that are not built yet are listed here so the matrix is complete and testable.
"""

from __future__ import annotations

POLICY_VERSION = "2026-11-27.2"

ROLE_NAMES = ("owner", "admin", "demo_reviewer", "operator", "inventory_manager", "marketing_manager", "auditor")

ORG_READ = {"org.business.read", "org.location.read", "org.settings.read"}
ORG_WRITE = {"org.business.write", "org.location.write", "org.settings.write"}
IDENTITY = {"identity.membership.read", "identity.membership.write"}
CRM_READ = {"crm.customer.read", "crm.consent.read"}
CRM_WRITE = {"crm.customer.write", "crm.consent.write"}
CONV_READ = {"conversations.thread.read"}
CONV_WRITE = {"conversations.thread.write"}
CATALOG_READ = {"catalog.item.read"}
CATALOG_WRITE = {"catalog.item.write"}
SCHED_READ = {"scheduling.resource.read", "scheduling.appointment.read"}
SCHED_WRITE = {"scheduling.resource.write", "scheduling.appointment.write"}
INV_READ = {"inventory.stock.read", "inventory.reorder.read"}
INV_WRITE = {"inventory.stock.adjust", "inventory.reorder.write"}
MKT_READ = {"marketing.campaign.read"}
MKT_WRITE = {"marketing.campaign.write"}
APPROVALS = {"approvals.proposal.read", "approvals.proposal.create"}
AUDIT_READ = {"audit.event.read"}
OUTBOX = {"notifications.outbox.read", "notifications.outbox.resolve"}
SALES_READ = {"sales.document.read"}
SALES_WRITE = {"sales.document.write", "sales.document.issue", "sales.invoice.mark_paid"}
SALES_ADMIN = {"sales.invoice.credit", "sales.proposal.approve"}
PURCH_READ = {"purchasing.po.read"}
PURCH_WRITE = {"purchasing.po.write", "purchasing.po.receive"}
PURCH_ADMIN = {"purchasing.po.approve", "purchasing.receipt.override"}
BILLS_READ = {"purchasing.bill.read"}
BILLS_WRITE = {"purchasing.bill.write"}
BILLS_ADMIN = {"purchasing.bill.pay", "purchasing.bill.void"}
AI_USE = {"ai.assistant.use"}
ACCOUNTING_READ = {"accounting.sync.read"}
ACCOUNTING_RUN = {"accounting.sync.run"}
# Linking the books to QuickBooks hands Intuit a sign-in to the business: owner only.
ACCOUNTING_CONNECT = {"accounting.connection.manage"}
PAYMENTS_READ = {"payments.connection.read"}
# Linking a Stripe account and turning online payments on or off: owner or admin (M21 rule 1).
PAYMENTS_MANAGE = {"payments.connection.manage"}
# Making and sharing an invoice's pay link, and seeing its online payments: whoever can issue invoices.
PAYMENTS_LINK = {"payments.link.share"}

APPROVE = {
    "crm": "crm.proposal.approve",
    "scheduling": "scheduling.proposal.approve",
    "conversations": "conversations.proposal.approve",
    "inventory": "inventory.proposal.approve",
    "catalog": "catalog.proposal.approve",
    "marketing": "marketing.proposal.approve",
}

_ADMIN = (
    ORG_READ
    | ORG_WRITE
    | IDENTITY
    | CRM_READ
    | CRM_WRITE
    | CONV_READ
    | CONV_WRITE
    | CATALOG_READ
    | CATALOG_WRITE
    | SCHED_READ
    | SCHED_WRITE
    | {"scheduling.appointment.override"}
    | INV_READ
    | INV_WRITE
    | MKT_READ
    | MKT_WRITE
    | SALES_READ
    | SALES_WRITE
    | PURCH_READ
    | PURCH_WRITE
    | PURCH_ADMIN
    | BILLS_READ
    | BILLS_WRITE
    | BILLS_ADMIN
    | SALES_ADMIN
    | ACCOUNTING_READ
    | ACCOUNTING_RUN
    | PAYMENTS_READ
    | PAYMENTS_LINK
    | APPROVALS
    | AUDIT_READ
    | OUTBOX
    | AI_USE
    | {"ai.settings.read", "admin_ops.system.read"}
    | set(APPROVE.values())
)

ROLES: dict[str, frozenset[str]] = {
    "owner": frozenset(
        _ADMIN
        | {"org.business.admin", "identity.owner.grant", "audit.export", "ai.settings.write", "admin_ops.data.manage"}
        | ACCOUNTING_CONNECT
        | PAYMENTS_MANAGE
    ),
    "admin": frozenset(_ADMIN | PAYMENTS_MANAGE),
    "demo_reviewer": frozenset(_ADMIN - ORG_WRITE - {"identity.membership.write"}),
    "operator": frozenset(
        ORG_READ
        | CRM_READ
        | CRM_WRITE
        | CONV_READ
        | CONV_WRITE
        | CATALOG_READ
        | SCHED_READ
        | {"scheduling.appointment.write"}
        | INV_READ
        | SALES_READ
        | PURCH_READ
        | {"purchasing.po.receive"}
        | SALES_WRITE
        | PAYMENTS_LINK
        | APPROVALS
        | OUTBOX
        | AI_USE
        | {APPROVE["crm"], APPROVE["scheduling"], APPROVE["conversations"]}
    ),
    "inventory_manager": frozenset(
        ORG_READ
        | {"crm.customer.read"}
        | CATALOG_READ
        | CATALOG_WRITE
        | SCHED_READ
        | INV_READ
        | PURCH_READ
        | PURCH_WRITE
        | BILLS_READ
        | BILLS_WRITE
        | INV_WRITE
        | APPROVALS
        | AI_USE
        | {APPROVE["inventory"], APPROVE["catalog"]}
    ),
    "marketing_manager": frozenset(
        ORG_READ
        | CRM_READ
        | CONV_READ
        | CATALOG_READ
        | MKT_READ
        | MKT_WRITE
        | APPROVALS
        | AI_USE
        | {APPROVE["marketing"]}
    ),
    "auditor": frozenset(
        ORG_READ
        | {"identity.membership.read"}
        | CRM_READ
        | CONV_READ
        | CATALOG_READ
        | SCHED_READ
        | INV_READ
        | MKT_READ
        | SALES_READ
        | PURCH_READ
        | BILLS_READ
        | ACCOUNTING_READ
        | {"approvals.proposal.read"}
        | AUDIT_READ
        | {"audit.export", "ai.settings.read"}
    ),
}

ALL_PERMISSIONS: frozenset[str] = frozenset().union(*ROLES.values())


def permissions_for(roles: set[str] | frozenset[str]) -> frozenset[str]:
    return frozenset().union(*(ROLES[r] for r in roles if r in ROLES))
