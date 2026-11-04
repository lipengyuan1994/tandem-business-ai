import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const terms = { customer: "patient", customers: "patients", service: "treatment", services: "treatments", technician: "dentist", technicians: "dentists" }
const settings = { hold_minutes: 30, booking_lead_time_minutes: 60, retention_days_conversations: 730, ai_policy: "strict_private", sales_tax_rate: "0", terminology: terms }

describe("business terminology", () => {
  it("applies overrides to navigation, page titles, search and main buttons", async () => {
    mockMe(meWith("crm.customer.read", "crm.customer.write"))
    server.use(
      http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings, version: 1 })),
      http.get("*/api/v1/crm/customers", () => HttpResponse.json({ items: [], next_cursor: null })),
    )
    renderApp("/customers")
    expect(await screen.findByRole("link", { name: "Patients" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Patients" })).toBeInTheDocument()
    expect(screen.getByLabelText("Search patients")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "New patient" })).toBeInTheDocument()
  })

  it("edits terminology in policies and refreshes navigation after saving", async () => {
    mockMe(meWith("org.settings.read", "org.settings.write", "crm.customer.read"))
    let submitted: unknown
    server.use(
      http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings, version: 1 })),
      http.patch("*/api/v1/org/settings", async ({ request }) => {
        const body = await request.json() as { settings: object }
        submitted = body
        return HttpResponse.json({ settings: body.settings, version: 2 })
      }),
    )
    renderApp("/settings/policies")
    const input = await screen.findByLabelText("customers", { exact: true })
    await userEvent.clear(input)
    await userEvent.type(input, "clients")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(submitted).toMatchObject({ version: 1, settings: { terminology: { customers: "clients" } } }))
    expect(await screen.findByRole("link", { name: "Clients" })).toBeInTheDocument()
  })

  it("keeps default terminology for another business without overrides", async () => {
    mockMe(meWith("crm.customer.read"))
    server.use(http.get("*/api/v1/crm/customers", () => HttpResponse.json({ items: [], next_cursor: null })))
    renderApp("/customers")
    expect(await screen.findByRole("heading", { name: "Customers" })).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Patients" })).not.toBeInTheDocument()
  })
})
