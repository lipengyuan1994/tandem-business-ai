import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const teamRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/team", component: lazyRouteComponent(() => import("./TeamPage"), "TeamPage") })
export const teamDetailRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/team/$key", component: lazyRouteComponent(() => import("./TeamPage"), "AgentDetailPage") })
export const chainRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/chains/$id", component: lazyRouteComponent(() => import("./ChainPage"), "ChainPage") })

export const autonomyRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/autonomy", validateSearch: (search: Record<string, unknown>) => ({ action: typeof search.action === "string" ? search.action : undefined }), component: lazyRouteComponent(() => import("./AutonomyPage"), "AutonomyPage") })
export const activityRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/activity", validateSearch: (search: Record<string, unknown>) => ({ agent: typeof search.agent === "string" ? search.agent : undefined }), component: lazyRouteComponent(() => import("./ActivityPage"), "ActivityPage") })

export const workQueueRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/work-queue", validateSearch: (search: Record<string, unknown>) => ({ agent: typeof search.agent === "string" ? search.agent : undefined }), component: lazyRouteComponent(() => import("./WorkQueuePage"), "WorkQueuePage") })
export const exceptionsRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/exceptions", component: lazyRouteComponent(() => import("./WorkQueuePage"), "ExceptionsPage") })
