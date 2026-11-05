import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const autonomyRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/autonomy", component: lazyRouteComponent(() => import("./AutonomyPage"), "AutonomyPage") })
export const activityRoute = createRoute({ getParentRoute: () => appRoute, path: "/ai/activity", component: lazyRouteComponent(() => import("./ActivityPage"), "ActivityPage") })
