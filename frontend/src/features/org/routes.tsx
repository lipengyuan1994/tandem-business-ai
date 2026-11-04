// Settings pages load on demand (route-level code splitting, 09 performance budget).
import { createRoute, lazyRouteComponent, redirect } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"
import { SettingsLayout } from "./pages/SettingsLayout"

const settingsRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings", component: SettingsLayout })

export const settingsTree = settingsRoute.addChildren([
  createRoute({ getParentRoute: () => settingsRoute, path: "/", beforeLoad: () => { throw redirect({ to: "/settings/business" }) } }),
  createRoute({ getParentRoute: () => settingsRoute, path: "/business", component: lazyRouteComponent(() => import("./pages/BusinessSettingsPage"), "BusinessSettingsPage") }),
  createRoute({ getParentRoute: () => settingsRoute, path: "/locations", component: lazyRouteComponent(() => import("./pages/LocationsPage"), "LocationsPage") }),
  createRoute({ getParentRoute: () => settingsRoute, path: "/policies", component: lazyRouteComponent(() => import("./pages/PoliciesPage"), "PoliciesPage") }),
  createRoute({ getParentRoute: () => settingsRoute, path: "/ai", component: lazyRouteComponent(() => import("@/features/ai/AiSettingsPage"), "AiSettingsPage") }),
  createRoute({ getParentRoute: () => settingsRoute, path: "/staff", component: lazyRouteComponent(() => import("@/features/identity/pages/StaffPage"), "StaffPage") }),
])
