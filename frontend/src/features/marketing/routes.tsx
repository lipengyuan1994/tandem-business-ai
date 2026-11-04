// Pages load on demand (route-level code splitting, 09 performance budget).
import { createRoute, lazyRouteComponent } from "@tanstack/react-router"
import { appRoute } from "@/app/routes-base"

export const marketingRoute = createRoute({ getParentRoute: () => appRoute, path: "/marketing", component: lazyRouteComponent(() => import("./MarketingPage"), "MarketingPage") })
export const campaignRoute = createRoute({ getParentRoute: () => appRoute, path: "/marketing/$campaignId", component: lazyRouteComponent(() => import("./CampaignPage"), "CampaignPage") })
