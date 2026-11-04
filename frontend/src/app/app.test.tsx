import { screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ME, mockMe, renderApp } from "@/test/app"

describe("session gate", () => {
  afterEach(() => vi.restoreAllMocks())

  it("sends signed-out users to Keycloak login with a return path", async () => {
    const assign = vi.fn()
    vi.stubGlobal("location", { ...window.location, assign })
    mockMe(null)
    renderApp("/approvals")
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/api/v1/auth/login?return_to=%2Fapprovals"))
    vi.unstubAllGlobals()
  })

  it("shows the business picker when no business is active", async () => {
    mockMe({ ...ME, active_business_id: null, active_business_timezone: null, permissions: [] })
    const { router } = renderApp("/today")
    expect(await screen.findByRole("heading", { name: "Choose a business" })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/select-business")
    expect(screen.getByText("Example Cycle Works")).toBeInTheDocument()
  })

  it("renders the shell with navigation filtered by permissions", async () => {
    mockMe()
    renderApp("/today")
    const nav = await screen.findByRole("navigation", { name: "Main" })
    expect(nav).toHaveTextContent("Approvals")
    expect(nav).toHaveTextContent("Audit")
    expect(nav).not.toHaveTextContent("Inventory") // no inventory.stock.read
    expect(screen.getByRole("button", { name: /Account menu/ })).toHaveTextContent("Morgan Example")
  })
})
