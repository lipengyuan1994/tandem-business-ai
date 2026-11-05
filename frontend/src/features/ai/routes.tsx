import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const autonomyRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/autonomy", component: lazyRouteComponent(() => import("./AutonomyPage"), "AutonomyPage") })
export const activityRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/activity", component: lazyRouteComponent(() => import("./ActivityPage"), "ActivityPage") })

export const workQueueRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/work-queue", component: lazyRouteComponent(() => import("./WorkQueuePage"), "WorkQueuePage") })
export const exceptionsRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/exceptions", component: lazyRouteComponent(() => import("./WorkQueuePage"), "ExceptionsPage") })
