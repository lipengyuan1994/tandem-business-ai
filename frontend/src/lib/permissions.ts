// Navigation and permission helpers. Permission strings come from /auth/me (06-security-and-privacy.md).
export type PermissionSet = ReadonlySet<string>

/** A list means any one of them is enough. */
export function hasPermission(permissions: PermissionSet, permission: string | readonly string[] | undefined): boolean {
  if (permission === undefined) return true
  return typeof permission === "string" ? permissions.has(permission) : permission.some((p) => permissions.has(p))
}

export interface NavItem {
  to: string
  labelKey: string
  /** Any one of a list is enough. */
  permission?: string | readonly string[]
  group?: "ai"
  /** Key in /api/v1/dashboard/counters shown as a badge. */
  counter?: "inbox_waiting_on_staff" | "approvals_i_can_decide"
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/today", labelKey: "nav.today" },
  { to: "/inbox", labelKey: "nav.inbox", permission: "conversations.thread.read", counter: "inbox_waiting_on_staff" },
  { to: "/knowledge", labelKey: "nav.knowledge", permission: "conversations.thread.read" },
  { to: "/approvals", labelKey: "nav.approvals", permission: "approvals.proposal.read", counter: "approvals_i_can_decide" },
  { to: "/customers", labelKey: "nav.customers", permission: "crm.customer.read" },
  { to: "/schedule", labelKey: "nav.schedule", permission: "scheduling.appointment.read" },
  { to: "/inventory", labelKey: "nav.inventory", permission: "inventory.stock.read" },
  { to: "/catalog", labelKey: "nav.catalog", permission: "catalog.item.read" },
  { to: "/marketing", labelKey: "nav.marketing", permission: "marketing.campaign.read" },
  { to: "/purchasing", labelKey: "nav.purchasing", permission: "purchasing.po.read" },
  { to: "/sales", labelKey: "nav.sales", permission: "sales.document.read" },
  { to: "/reports", labelKey: "nav.reports", permission: ["sales.document.read", "purchasing.po.read"] },
  { to: "/audit", labelKey: "nav.audit", permission: "audit.event.read" },
  { to: "/system", labelKey: "nav.system", permission: "admin_ops.system.read" },
  { to: "/ai/team", labelKey: "ai.team.nav", permission: "approvals.proposal.read", group: "ai" },
  { to: "/settings/ai", labelKey: "ai.settings.nav", permission: "ai.settings.read", group: "ai" },
  { to: "/ai/autonomy", labelKey: "ai.autonomy.title", permission: "org.settings.write", group: "ai" },
  { to: "/ai/activity", labelKey: "ai.activity.title", permission: "approvals.proposal.read", group: "ai" },
  { to: "/ai/work-queue", labelKey: "ai.workQueue.title", permission: "approvals.proposal.read", group: "ai" },
  { to: "/ai/exceptions", labelKey: "ai.exceptions.title", permission: "approvals.proposal.read", group: "ai" },
  { to: "/settings", labelKey: "nav.settings", permission: "org.business.read" },
]
