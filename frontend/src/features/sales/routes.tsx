import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const salesRoute = createRoute({ getParentRoute: () => appRoute, path: "/sales", component: lazyRouteComponent(() => import("./SalesPage"), "SalesPage") })
export const quoteRoute = createRoute({ getParentRoute: () => appRoute, path: "/sales/quotes/$id", component: lazyRouteComponent(() => import("./DocumentPage"), "QuotePage") })
export const orderRoute = createRoute({ getParentRoute: () => appRoute, path: "/sales/orders/$id", component: lazyRouteComponent(() => import("./DocumentPage"), "OrderPage") })
export const invoiceRoute = createRoute({ getParentRoute: () => appRoute, path: "/sales/invoices/$id", component: lazyRouteComponent(() => import("./DocumentPage"), "InvoicePage") })
