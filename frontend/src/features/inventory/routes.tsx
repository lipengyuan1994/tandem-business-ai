// Pages load on demand (route-level code splitting, 09 performance budget).
import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const inventoryRoute = createRoute({ getParentRoute: () => appRoute, path: "/inventory", component: lazyRouteComponent(() => import("./InventoryPage"), "InventoryPage") })
export const inventoryItemRoute = createRoute({ getParentRoute: () => appRoute, path: "/inventory/$itemId", component: lazyRouteComponent(() => import("./InventoryItemPage"), "InventoryItemPage") })
