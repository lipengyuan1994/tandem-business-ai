import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { CONNECTION_MANAGE, CONNECTION_READ, type PaymentConnection } from "./api"

// Synthetic data only.
const ROOT = "*/api/v1/payments/stripe"
const notConnected: PaymentConnection = {
  configured: true, status: "not_connected", business_currency: "USD", charges_enabled: false, payouts_enabled: false,
  details_submitted: false, requirements_due: [], version: null,
}
const onboarding: PaymentConnection = {
  ...notConnected, status: "onboarding", mode: "test", stripe_account_id: "acct_test0001", country: "US", default_currency: "usd",
  requirements_due: ["external_account", "business_profile.url"], dashboard_url: "https://dashboard.stripe.com/test", version: 2,
}
const active: PaymentConnection = {
  ...onboarding, status: "active", charges_enabled: true, payouts_enabled: true, details_submitted: true, requirements_due: [], version: 3,
}

function mocks(connection: PaymentConnection, ...permissions: string[]) {
  mockMe(meWith(CONNECTION_READ, ...permissions))
  server.use(
    http.get(ROOT, () => HttpResponse.json(connection)),
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD", timezone: "America/Denver" })),
  )
}

afterEach(() => { window.history.replaceState(null, "", "/"); vi.restoreAllMocks() })

describe("payments settings", () => {
  it("shows the tab only with payments access", async () => {
    mockMe(meWith("org.business.read"))
    renderApp("/settings/business")
    expect(await screen.findByRole("link", { name: "Business" })).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Payments" })).not.toBeInTheDocument()
  })

  it("sends an owner to Stripe's onboarding with the chosen country", async () => {
    mocks(notConnected, CONNECTION_MANAGE)
    const assign = vi.fn()
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign, search: "" })
    let body: Record<string, unknown> = {}
    server.use(http.post(`${ROOT}/onboarding`, async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>
      return HttpResponse.json({ url: "https://connect.stripe.com/setup/s/example" })
    }))
    renderApp("/settings/payments")
    await userEvent.selectOptions(await screen.findByLabelText("Country the business is registered in"), "CA")
    await userEvent.click(screen.getByRole("button", { name: "Set up with Stripe" }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://connect.stripe.com/setup/s/example"))
    expect(body).toEqual({ country: "CA" })
  })

  it("lets staff who can only read see the status but not change it", async () => {
    mocks(onboarding)
    renderApp("/settings/payments")
    expect(await screen.findByText("Setting up")).toBeInTheDocument()
    expect(screen.getByText("Bank account for payouts")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Continue in Stripe" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Turn off online payments" })).not.toBeInTheDocument()
  })

  it("checks the account straight away when Stripe sends the owner back", async () => {
    mocks(onboarding, CONNECTION_MANAGE)
    let refreshed = 0
    server.use(http.post(`${ROOT}/refresh`, () => { refreshed += 1; return HttpResponse.json(active) }))
    renderApp("/settings/payments?stripe=return")
    expect(await screen.findByText("Ready")).toBeInTheDocument()
    expect(refreshed).toBe(1)
    expect(screen.getByRole("link", { name: "Open Stripe Dashboard" })).toHaveAttribute("href", "https://dashboard.stripe.com/test")
  })

  it("turns payments off with the version after confirming", async () => {
    mocks(active, CONNECTION_MANAGE)
    let body: Record<string, unknown> = {}
    server.use(http.post(`${ROOT}/disconnect`, async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>
      return HttpResponse.json({ ...active, status: "disconnected", version: 4 })
    }))
    renderApp("/settings/payments")
    await userEvent.click(await screen.findByRole("button", { name: "Turn off online payments" }))
    const dialog = await screen.findByRole("dialog")
    await userEvent.click(within(dialog).getByRole("button", { name: "Turn off online payments" }))
    await waitFor(() => expect(body).toEqual({ version: 3 }))
  })

  it("says when the server has no payments relay", async () => {
    mocks({ ...notConnected, configured: false }, CONNECTION_MANAGE)
    renderApp("/settings/payments")
    expect(await screen.findByText(/Online payments aren't set up on this server yet/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Set up with Stripe" })).not.toBeInTheDocument()
  })
})
