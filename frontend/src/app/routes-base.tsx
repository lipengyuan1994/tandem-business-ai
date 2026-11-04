// Root and signed-in shell routes. Features import these as parents so their routes stay fully typed.
import type { QueryClient } from "@tanstack/react-query"
import { Outlet, createRootRouteWithContext, createRoute } from "@tanstack/react-router"
import { Toaster } from "@/components/ui/sonner"
import { AuthGate } from "./auth-gate"
import { NotFound, RouteError } from "./error-boundary"
import { AppLayout } from "./layout"

export interface RouterContext {
  queryClient: QueryClient
}

export const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: () => (
    <>
      <Outlet />
      <Toaster />
    </>
  ),
  notFoundComponent: NotFound,
  errorComponent: RouteError,
})

/** Everything inside the signed-in shell. */
export const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  component: () => (
    <AuthGate>
      <AppLayout />
    </AuthGate>
  ),
})
