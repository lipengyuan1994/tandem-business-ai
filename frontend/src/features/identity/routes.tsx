import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { rootRoute } from "@/app/routes-base"

/** Outside the signed-in shell. */
export const selectBusinessRoute = createRoute({ getParentRoute: () => rootRoute, path: "/select-business", component: lazyRouteComponent(() => import("./pages/SelectBusinessPage"), "SelectBusinessPage") })
export const loginFailedRoute = createRoute({ getParentRoute: () => rootRoute, path: "/login-failed", component: lazyRouteComponent(() => import("./pages/LoginFailedPage"), "LoginFailedPage") })
