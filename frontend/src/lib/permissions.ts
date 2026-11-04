// Navigation and permission helpers. Permission strings come from /auth/me (06-security-and-privacy.md).
export type PermissionSet = ReadonlySet<string>

export function hasPermission(permissions: PermissionSet, permission: string | undefined): boolean {
  return permission === undefined || permissions.has(permission)
}

export interface NavItem {
  to: string
  labelKey: string
  permission?: string
  /** Key in /api/v1/dashboard/counters shown as a badge. */
  counter?: "inbox_waiting_on_staff" | "approvals_i_can_decide"
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/today", labelKey: "nav.today" },
  { to: "/inbox", labelKey: "nav.inbox", permission: "conversations.thread.read", counter: "inbox_waiting_on_staff" },
  { to: "/approvals", labelKey: "nav.approvals", permission: "approvals.proposal.read", counter: "approvals_i_can_decide" },
  { to: "/customers", labelKey: "nav.customers", permission: "crm.customer.read" },
  { to: "/schedule", labelKey: "nav.schedule", permission: "scheduling.appointment.read" },
  { to: "/inventory", labelKey: "nav.inventory", permission: "inventory.stock.read" },
  { to: "/catalog", labelKey: "nav.catalog", permission: "catalog.item.read" },
  { to: "/marketing", labelKey: "nav.marketing", permission: "marketing.campaign.read" },
  { to: "/purchasing", labelKey: "nav.purchasing", permission: "purchasing.po.read" },
  { to: "/sales", labelKey: "nav.sales", permission: "sales.document.read" },
  { to: "/audit", labelKey: "nav.audit", permission: "audit.event.read" },
  { to: "/system", labelKey: "nav.system", permission: "admin_ops.system.read" },
  { to: "/settings", labelKey: "nav.settings", permission: "org.business.read" },
]
