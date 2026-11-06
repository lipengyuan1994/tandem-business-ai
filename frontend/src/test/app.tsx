// Render the real route tree at a path, with a fresh QueryClient and in-memory history.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router"
import { render } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import type { Me } from "@/api/types"
import { routeTree } from "@/app/router"
import { server } from "./server"

export const ME: Me = {
  user: { id: "00000000-0000-7000-8000-000000000001", display_name: "Morgan Example" },
  email: "morgan@example.com",
  memberships: [{ business_id: "00000000-0000-7000-8000-0000000000b1", business_name: "Example Cycle Works", business_slug: "example-cycle-works", roles: ["owner"] }],
  active_business_id: "00000000-0000-7000-8000-0000000000b1",
  active_business_timezone: "America/Denver",
  permissions: ["approvals.proposal.read", "approvals.proposal.create", "crm.proposal.approve", "identity.membership.read", "identity.membership.write", "identity.owner.grant", "org.business.read", "audit.event.read"],
  csrf_token: "csrf-test-token",
  policy_version: "test",
}

/** ME with extra permissions (e.g. a page's read/write permissions). */
export function meWith(...permissions: string[]): Me {
  return { ...ME, permissions: [...ME.permissions, ...permissions] }
}

export function mockMe(me: Me | null = ME) {
  server.use(
    http.get("*/api/v1/ai/agents", () => HttpResponse.json({ items: [] })),
    http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings: { terminology: {} }, version: 1 })),
    http.get("*/api/v1/admin/system", () => HttpResponse.json({ data_profile: "standard" })),
    http.get("*/api/v1/auth/me", () => (me ? HttpResponse.json(me) : HttpResponse.json({ code: "auth.unauthenticated", status: 401, title: "x" }, { status: 401 }))),
    http.get("*/api/v1/notifications", () => HttpResponse.json({ items: [], unread: 0 })),
    http.get("*/api/v1/dashboard/counters", () => HttpResponse.json({ approvals_i_can_decide: 0, inbox_waiting_on_staff: 0, unread_notifications: 0 })),
  )
}

export function renderApp(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({ routeTree, context: { queryClient }, history: createMemoryHistory({ initialEntries: [path] }) })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...view, router, queryClient }
}
