import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { Appointment, SalesDocumentCreate } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { CUSTOMER, doc, ID, ITEM, mockDocument, mockPickers, PERMS } from "./test-support"

function mockLists() {
  server.use(http.get("*/api/v1/sales/:family", () => HttpResponse.json({ items: [], next_cursor: null })))
}
describe("sales forms", () => {
  it.each(["quote", "invoice"])("creates a %s using customer search, catalog and free text with an explicit zero tax override", async (kind) => {
    mockMe(meWith(...PERMS)); mockLists(); mockPickers(); mockDocument(doc(), `${kind}s`)
    let sent: SalesDocumentCreate | undefined
    server.use(http.post(`*/api/v1/sales/${kind}s`, async ({ request }) => { sent = await request.json() as SalesDocumentCreate; return HttpResponse.json(doc(), { status: 201 }) }))
    const { router } = renderApp("/sales")
    await userEvent.click(await screen.findByRole("button", { name: `New ${kind}` }))
    const dialog = screen.getByRole("dialog")
    const create = within(dialog).getByRole("button", { name: "Create draft" })
    expect(create).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText("Customer"), "Ada")
    await userEvent.click(await within(dialog).findByRole("button", { name: "Ada Example" }))
    await userEvent.selectOptions(await within(dialog).findByLabelText("Catalog item"), ITEM.id)
    expect(within(dialog).getByLabelText("Description")).toBeDisabled()
    expect(within(dialog).getByLabelText(/^Unit price\b/)).toHaveValue("89.00")
    await userEvent.click(within(dialog).getByRole("button", { name: "Add item" }))
    const line2 = within(dialog).getByRole("group", { name: "Item 2" })
    await userEvent.type(within(line2).getByLabelText("Description"), "Wheel alignment")
    await userEvent.type(within(line2).getByLabelText(/^Unit price\b/), "25.00")
    await userEvent.type(within(line2).getByLabelText("Tax rate (%)"), "0")
    await userEvent.click(create)
    await waitFor(() => expect(sent?.customer_id).toBe(CUSTOMER))
    expect(sent?.lines).toEqual([{ catalog_item_id: ITEM.id, quantity: "1" }, { name: "Wheel alignment", quantity: "1", unit_price_minor: 2500, tax_rate: "0.0000" }])
    await waitFor(() => expect(router.state.location.pathname).toBe(`/sales/${kind}s/${ID}`))
  })

  it.each([
    ["USD", "25.50", 2550, "89.00"],
    ["JPY", "2500", 2500, "8900"],
    ["KWD", "25.123", 25123, "8.900"],
  ])("creates a draft using the business currency %s and sends exact minor units", async (currency, amount, minor, catalogPrice) => {
    mockMe(meWith(...PERMS)); mockLists(); mockPickers(currency); mockDocument(doc({ currency }))
    let sent: SalesDocumentCreate | undefined
    server.use(http.post("*/api/v1/sales/invoices", async ({ request }) => { sent = await request.json() as SalesDocumentCreate; return HttpResponse.json(doc({ currency }), { status: 201 }) }))
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("button", { name: "New invoice" }))
    const dialog = screen.getByRole("dialog")
    await userEvent.type(within(dialog).getByLabelText("Customer"), "Ada")
    await userEvent.click(await within(dialog).findByRole("button", { name: "Ada Example" }))
    await userEvent.selectOptions(await within(dialog).findByLabelText("Catalog item"), ITEM.id)
    expect(within(dialog).getByLabelText(`Unit price (${currency})`)).toHaveValue(catalogPrice)
    await userEvent.selectOptions(within(dialog).getByLabelText("Catalog item"), "")
    const price = within(dialog).getByLabelText(/^Unit price\b/)
    await userEvent.clear(price); await userEvent.type(price, amount)
    await userEvent.click(within(dialog).getByRole("button", { name: "Create draft" }))
    await waitFor(() => expect(sent?.lines[0]).toEqual({ name: "Brake service", quantity: "1", unit_price_minor: minor }))
  })

  it.each([
    ["USD", "89.00", "0.29", 29, "0.291", 2],
    ["JPY", "8900", "2500", 2500, "2500.1", 0],
    ["KWD", "8.900", "25.123", 25123, "25.1234", 3],
  ])("edits %s prices in normal money, rejects extra precision and saves exact minor units", async (currency, initialPrice, amount, minor, invalid, digits) => {
    mockMe(meWith(...PERMS)); mockDocument(doc({ currency })); mockPickers()
    let sent: unknown = null
    server.use(http.patch(`*/api/v1/sales/invoices/${ID}`, async ({ request }) => { sent = await request.json(); return HttpResponse.json(doc({ currency, version: 4 })) }))
    renderApp(`/sales/invoices/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit items" }))
    const price = screen.getByLabelText(`Unit price (${currency})`)
    expect(price).toHaveValue(initialPrice)
    await userEvent.clear(price); await userEvent.type(price, invalid)
    expect(screen.getByText(`${currency} allows up to ${digits} decimal places.`)).toBeInTheDocument()
    expect(price).toHaveAttribute("aria-invalid", "true")
    expect(price).toHaveAccessibleDescription(`${currency} allows up to ${digits} decimal places.`)
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    expect(sent).toBeNull()
    await userEvent.clear(price); await userEvent.type(price, amount)
    expect(price).toHaveAttribute("aria-invalid", "false")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toMatchObject({ version: 3, lines: [{ unit_price_minor: minor }] }))
  })

  it("blocks new drafts when the business currency cannot be loaded rather than assuming USD", async () => {
    mockMe(meWith(...PERMS)); mockLists()
    server.use(http.get("*/api/v1/org/business", () => HttpResponse.json({ title: "Currency unavailable", status: 503, code: "test.unavailable" }, { status: 503 })))
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("button", { name: "New quote" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Currency unavailable")
    expect(screen.getByRole("button", { name: "Create draft" })).toBeDisabled()
    expect(screen.queryByLabelText(/^Unit price\b/)).not.toBeInTheDocument()
  })

  it("maps server line validation to the input and retains the edit", async () => {
    mockMe(meWith(...PERMS)); mockDocument(); mockPickers()
    server.use(http.patch(`*/api/v1/sales/invoices/${ID}`, () => HttpResponse.json({ title: "Validation failed", status: 422, code: "platform.validation_error", errors: [{ field: "lines.0.quantity", code: "invalid" }] }, { status: 422 })))
    renderApp(`/sales/invoices/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit items" }))
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("Check this value and try again.")).toBeInTheDocument()
    expect(screen.getByLabelText("Quantity")).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByLabelText("Description")).toHaveValue("Brake service")
  })

  it("does not overwrite a document that becomes locked during conflict reload", async () => {
    mockMe(meWith(...PERMS)); mockDocument(); mockPickers()
    server.use(http.patch(`*/api/v1/sales/invoices/${ID}`, () => HttpResponse.json({ title: "Conflict", status: 409, code: "sales.version_conflict" }, { status: 409 })))
    renderApp(`/sales/invoices/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit items" }))
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await screen.findByText("This record changed since you opened it")
    mockDocument(doc({ status: "issued", version: 4 }))
    await userEvent.click(screen.getByRole("button", { name: "Reload" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeDisabled())
    expect(screen.getByLabelText("Quantity")).toBeDisabled()
  })
})

const appointment: Appointment = {
  id: "appt1", customer_id: CUSTOMER, customer_name: "Ada Example", service_id: ITEM.id, service_name: "Brake service", resource_id: "r1", resource_name: "Sam Example",
  location_id: "l1", starts_at: "2026-10-27T18:00:00Z", ends_at: "2026-10-27T19:00:00Z", blocked_until: "2026-10-27T19:00:00Z", timezone: "America/Denver",
  status: "completed", hold_expires_at: null, proposal_id: null, source: "staff", notes: null, cancelled_reason: null, version: 3,
}
function mockAppointment(status = "completed") {
  server.use(
    http.get("*/api/v1/scheduling/resources", () => HttpResponse.json([])),
    http.get("*/api/v1/scheduling/appointments", () => HttpResponse.json([])),
    http.get("*/api/v1/scheduling/appointments/appt1", () => HttpResponse.json({ ...appointment, status })),
  )
}
describe("appointment invoices", () => {
  it("creates from a completed appointment and navigates to the draft invoice", async () => {
    mockMe(meWith(...PERMS, "scheduling.appointment.read")); mockAppointment(); mockDocument()
    let called = 0
    server.use(http.post("*/api/v1/sales/invoices/from-appointment/appt1", () => { called++; return HttpResponse.json(doc(), { status: 201 }) }))
    const { router } = renderApp("/schedule?appointment=appt1")
    await userEvent.click(await screen.findByRole("button", { name: "Create invoice" }))
    await waitFor(() => expect(called).toBe(1))
    await waitFor(() => expect(router.state.location.pathname).toBe(`/sales/invoices/${ID}`))
    expect(await screen.findByRole("button", { name: "Edit items" })).toBeInTheDocument()
  })
  it.each([["completed", false], ["confirmed", true], ["cancelled", true]])("does not offer an invoice for status=%s, write=%s", async (status, write) => {
    mockMe(meWith("sales.document.read", "scheduling.appointment.read", ...(write ? ["sales.document.write"] : [])))
    mockAppointment(status)
    renderApp("/schedule?appointment=appt1")
    await screen.findByRole("dialog", { name: "Brake service · Ada Example" })
    expect(screen.queryByRole("button", { name: "Create invoice" })).not.toBeInTheDocument()
  })
})

describe("default sales tax settings", () => {
  it("validates a percentage and saves a decimal fraction without losing other settings", async () => {
    mockMe(meWith("org.settings.read", "org.settings.write"))
    let sent: unknown = null
    const settings = { version: 7, settings: { sales_tax_rate: "0.0000", hold_minutes: 30, ai_policy: "hosted_vultr", allow_backorders: false } }
    server.use(
      http.get("*/api/v1/org/settings", () => HttpResponse.json(settings)),
      http.patch("*/api/v1/org/settings", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ ...settings, version: 8 }) }),
    )
    renderApp("/settings/policies")
    const rate = await screen.findByLabelText("Default sales tax (%)")
    await userEvent.clear(rate); await userEvent.type(rate, "100.01")
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a percentage from 0 to 100")
    await userEvent.clear(rate); await userEvent.type(rate, "8.25")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toMatchObject({ version: 7, settings: { sales_tax_rate: "0.0825", hold_minutes: 30, allow_backorders: false } }))
  })
})
