// Assembles the route tree. Feature routes live in features/<module>/routes.tsx (parents from routes-base.tsx).
import type { QueryClient } from "@tanstack/react-query"
import { createRoute, createRouter, redirect } from "@tanstack/react-router"
import { approvalDetailRoute, approvalsRoute } from "@/features/approvals/routes"
import { teamRoute, teamDetailRoute, chainRoute, activityRoute, autonomyRoute, workQueueRoute, exceptionsRoute } from "@/features/ai/routes"
import { auditRoute } from "@/features/audit/routes"
import { catalogRoute } from "@/features/catalog/routes"
import { inboxRoute, inboxThreadRoute } from "@/features/conversations/routes"
import { campaignRoute, marketingRoute } from "@/features/marketing/routes"
import { systemRoute } from "@/features/system/routes"
import { invoiceRoute, orderRoute, quoteRoute, salesRoute } from "@/features/sales/routes"
import { customerDetailRoute, customersRoute } from "@/features/crm/routes"
import { inventoryItemRoute, inventoryRoute } from "@/features/inventory/routes"
import { scheduleRoute } from "@/features/scheduling/routes"
import { todayRoute } from "@/features/dashboard/routes"
import { loginFailedRoute, selectBusinessRoute } from "@/features/identity/routes"
import { settingsTree } from "@/features/org/routes"
import { purchasingRoute, purchaseOrderRoute, quoteRequestRoute } from "@/features/purchasing/routes"
import { knowledgeRoute, knowledgeNewRoute, knowledgeArticleRoute } from "@/features/knowledge/routes"
import { appRoute, rootRoute } from "./routes-base"

export type { RouterContext } from "./routes-base"

const indexRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/today" })
  },
})

export const routeTree = rootRoute.addChildren([
  selectBusinessRoute,
  loginFailedRoute,
  appRoute.addChildren([
    indexRoute, teamRoute, teamDetailRoute, chainRoute, knowledgeRoute, knowledgeNewRoute, knowledgeArticleRoute, autonomyRoute, activityRoute, workQueueRoute, exceptionsRoute, todayRoute, inboxRoute, inboxThreadRoute, approvalsRoute, approvalDetailRoute, customersRoute, customerDetailRoute,
    scheduleRoute, inventoryRoute, inventoryItemRoute, catalogRoute, marketingRoute, campaignRoute, salesRoute, quoteRoute, orderRoute, invoiceRoute, purchasingRoute, purchaseOrderRoute, quoteRequestRoute, auditRoute, systemRoute, settingsTree,
  ]),
])

export function buildRouter(queryClient: QueryClient) {
  return createRouter({ routeTree, context: { queryClient }, defaultPreload: "intent" })
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof buildRouter>
  }
}
