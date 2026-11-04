import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const systemRoute = createRoute({ getParentRoute: () => appRoute, path: "/system", component: lazyRouteComponent(() => import("./SystemPage"), "SystemPage") })
