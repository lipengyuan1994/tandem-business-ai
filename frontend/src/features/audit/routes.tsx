// Pages load on demand (route-level code splitting, 09 performance budget).
import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const auditRoute = createRoute({ getParentRoute: () => appRoute, path: "/audit", component: lazyRouteComponent(() => import("./AuditPage"), "AuditPage") })
