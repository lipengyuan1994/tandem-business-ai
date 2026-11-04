import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const purchasingRoute = createRoute({ getParentRoute: () => appRoute, path: "/purchasing", validateSearch: (search: Record<string, unknown>): { tab?: "orders" | "quotes" | "suppliers" | "suggestions"; late?: boolean } => ({ tab: search.tab === "quotes" || search.tab === "suppliers" || search.tab === "suggestions" ? search.tab : undefined, late: search.late === true || search.late === "true" ? true : undefined }), component: lazyRouteComponent(() => import("./PurchasingPage"), "PurchasingPage") })
export const purchaseOrderRoute = createRoute({ getParentRoute: () => appRoute, path: "/purchasing/purchase-orders/$id", component: lazyRouteComponent(() => import("./OrderPage"), "OrderPage") })

export const quoteRequestRoute = createRoute({ getParentRoute: () => appRoute, path: "/purchasing/quote-requests/$id", component: lazyRouteComponent(() => import("./QuoteRequestPage"), "QuoteRequestPage") })
