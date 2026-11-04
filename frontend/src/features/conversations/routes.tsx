// Pages load on demand (route-level code splitting, 09 performance budget).
import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const inboxRoute = createRoute({ getParentRoute: () => appRoute, path: "/inbox", component: lazyRouteComponent(() => import("./InboxPage"), "InboxPage") })
export const inboxThreadRoute = createRoute({ getParentRoute: () => appRoute, path: "/inbox/$conversationId", component: lazyRouteComponent(() => import("./InboxPage"), "InboxPage") })
