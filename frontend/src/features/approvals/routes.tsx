// Pages load on demand (route-level code splitting, 09 performance budget).
import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const approvalsRoute = createRoute({ getParentRoute: () => appRoute, path: "/approvals", component: lazyRouteComponent(() => import("./pages/ApprovalsPage"), "ApprovalsPage") })
export const approvalDetailRoute = createRoute({ getParentRoute: () => appRoute, path: "/approvals/$proposalId", component: lazyRouteComponent(() => import("./pages/ProposalDetailPage"), "ProposalDetailPage") })
