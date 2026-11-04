// Pages load on demand (route-level code splitting, 09 performance budget).
import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const customersRoute = createRoute({ getParentRoute: () => appRoute, path: "/customers", component: lazyRouteComponent(() => import("./pages/CustomersPage"), "CustomersPage") })
export const customerDetailRoute = createRoute({ getParentRoute: () => appRoute, path: "/customers/$customerId", component: lazyRouteComponent(() => import("./pages/CustomerDetailPage"), "CustomerDetailPage") })
