import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { APPROVE, OVERRIDE, READ, RECEIVE, WRITE, type PO, type Supplier } from "./model"

const ROOT = "*/api/v1/purchasing"
const supplier: Supplier = { active: true, id: "supplier-1", name: "Example Parts", email: "parts@example.com", phone: null, notes: null, version: 1, created_at: "2026-10-28T12:00:00Z", items: [{ catalog_item_id: "item-1", last_price_minor: 1250, supplier_sku: "BP-1", lead_time_days: 3 }] }
const po: PO = { id: "order-1", supplier_id: supplier.id, supplier_name: supplier.name, currency: "USD", status: "draft", number: null, approved_date: null, notes: null, total_minor: 2500, source_draft_id: null, source_quote_id: null, sent_at: null, expected_delivery_date: null, last_followup_at: null, version: 1, created_at: "2026-10-28T12:00:00Z", lines: [{ id: "line-1", catalog_item_id: "item-1", name: "Example pads", quantity: "2", unit_cost_minor: 1250, line_total_minor: 2500, position: 0, received_quantity: "0" }], receipts: [] }
function mocks() {
  mockMe(meWith(READ, WRITE, APPROVE, RECEIVE, OVERRIDE, "catalog.item.read"))
  server.use(
    http.get(`${ROOT}/purchase-orders`, () => HttpResponse.json({ items: [po], next_cursor: null })),
    http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json(po)),
    http.get(`${ROOT}/suppliers`, () => HttpResponse.json({ items: [supplier], next_cursor: null })),
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD" })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [{ id: "item-1", name: "Example pads", kind: "product", is_stock_tracked: true, status: "active" }], next_cursor: null })),
    http.get(`${ROOT}/reorder-suggestions`, () => HttpResponse.json([{ item_id: "item-1", item_name: "Example pads", available: "1", reorder_point: "5", target_level: "20", open_po_quantity: "10", suggested: "9", suppliers: [supplier] }])),
  )
}
describe("purchasing", () => {
  it("lists saved amounts and supplier contacts", async () => {
    mocks(); renderApp("/purchasing")
    expect(await screen.findByRole("link", { name: "Draft" })).toHaveAttribute("href", "/purchasing/purchase-orders/order-1")
    expect(screen.getByText("$25.00")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("tab", { name: "Suppliers" }))
    expect(await screen.findByText("parts@example.com")).toBeInTheDocument()
  })
  it("creates a draft from a suggestion with decimal costs sent as minor units", async () => {
    mocks()
    let body: unknown
    server.use(http.post(`${ROOT}/purchase-orders`, async ({ request }) => { body = await request.json(); return HttpResponse.json(po, { status: 201 }) }))
    const { queryClient } = renderApp("/purchasing")
    await userEvent.click(await screen.findByRole("tab", { name: "Reorder suggestions" }))
    await waitFor(() => expect(queryClient.getQueryState(["purchasing", "pickers"])).toMatchObject({status: "success"}))
    await waitFor(() => expect(screen.getByRole("button", { name: "Create PO · Example Parts" })).toBeEnabled()); await userEvent.click(screen.getByRole("button", { name: "Create PO · Example Parts" }))
    const dialog = screen.getByRole("dialog", { name: "New purchase order" })
    expect(within(dialog).getByLabelText("Unit cost (USD)")).toHaveValue("12.50")
    expect(within(dialog).getByLabelText("Quantity")).toHaveValue("9")
    await userEvent.click(within(dialog).getByRole("button", { name: "Create draft" }))
    await waitFor(() => expect(body).toMatchObject({ supplier_id: "supplier-1", lines: [{ catalog_item_id: "item-1", quantity: "9", unit_cost_minor: 1250 }] }))
  })
  it("requires confirmation before approving", async () => {
    mocks()
    let current = po
    let called = false
    let releaseRefresh = () => {}
    const refresh = new Promise<void>((resolve) => { releaseRefresh = resolve })
    server.use(
      http.get(`${ROOT}/purchase-orders/order-1`, async () => { if (called) await refresh; return HttpResponse.json(current) }),
      http.post(`${ROOT}/purchase-orders/order-1/approve`, async ({ request }) => {
        expect(await request.json()).toEqual({ version: 1 }); called = true
        current = { ...po, status: "approved", number: "PO-2026-0001", version: 2 }
        return HttpResponse.json(current)
      }),
    )
    renderApp("/purchasing/purchase-orders/order-1")
    await userEvent.click(await screen.findByRole("button", { name: "Approve PO" }))
    expect(called).toBe(false)
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Approve PO" }))
    await waitFor(() => expect(called).toBe(true))
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Approve PO" })).toBeDisabled()
    releaseRefresh()
    // Wait for invalidation and the success callback before the next test installs its handlers.
    expect(await screen.findByRole("button", { name: "Mark as sent" })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  })
  it("records per-line receipts and requires an override reason", async () => {
    mocks()
    server.use(http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json({ ...po, status: "sent" })))
    let body: unknown
    server.use(http.post(`${ROOT}/purchase-orders/order-1/receipts`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...po, status: "received" }, { status: 201 }) }))
    renderApp("/purchasing/purchase-orders/order-1")
    await userEvent.click(await screen.findByRole("button", { name: "Receive goods" }))
    const dialog = screen.getByRole("dialog")
    await userEvent.type(within(dialog).getByLabelText("Quantity received · Example pads"), "3")
    await userEvent.click(within(dialog).getByLabelText("Allow more than 110%"))
    expect(within(dialog).getByRole("button", { name: "Record receipt" })).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Extra pack")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record receipt" }))
    await waitFor(() => expect(body).toEqual({ version: 1, lines: [{ po_line_id: "line-1", quantity: "3" }], override: true, reason: "Extra pack" }))
  })
  it("lets operators receive without showing approval or override", async () => {
    mocks(); mockMe(meWith(READ, RECEIVE))
    server.use(http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json({ ...po, status: "sent" })))
    renderApp("/purchasing/purchase-orders/order-1")
    await userEvent.click(await screen.findByRole("button", { name: "Receive goods" }))
    expect(screen.queryByLabelText("Allow more than 110%")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Approve PO" })).not.toBeInTheDocument()
  })
  it("keeps auditor pages read-only", async () => {
    mocks(); mockMe(meWith(READ)); renderApp("/purchasing/purchase-orders/order-1")
    expect(await screen.findByText("Example pads")).toBeInTheDocument()
    for (const name of ["Approve PO", "Edit purchase order", "Receive goods", "Cancel PO"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument()
  })
  it("does not fetch purchasing data without read access", async () => {
    mockMe(); renderApp("/purchasing")
    expect(await screen.findByText("You do not have access to purchasing.")).toBeInTheDocument()
    expect(screen.queryByRole("tab")).not.toBeInTheDocument()
  })
  it("renders empty orders", async () => {
    mocks(); server.use(http.get(`${ROOT}/purchase-orders`, () => HttpResponse.json({ items: [], next_cursor: null })))
    renderApp("/purchasing"); expect(await screen.findByText("No purchase orders yet")).toBeInTheDocument()
  })
  it("renders loading before the list arrives", async () => {
    mocks(); server.use(http.get(`${ROOT}/purchase-orders`, () => new Promise(() => {})))
    renderApp("/purchasing"); expect(await screen.findByLabelText("Loading…")).toBeInTheDocument()
  })
  it("shows a list error with retry", async () => {
    mocks(); server.use(http.get(`${ROOT}/purchase-orders`, () => HttpResponse.json({ status: 500, code: "test.failed", title: "Example failure" }, { status: 500 })))
    renderApp("/purchasing"); expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument()
  })
  it("saves supplier items as decimal money with lead time and contact data", async () => {
    mocks()
    let body: unknown
    server.use(http.post(`${ROOT}/suppliers`, async ({ request }) => { body = await request.json(); return HttpResponse.json(supplier, { status: 201 }) }))
    renderApp("/purchasing")
    await userEvent.click(await screen.findByRole("tab", { name: "Suppliers" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "New supplier" })).toBeEnabled())
    await userEvent.click(screen.getByRole("button", { name: "New supplier" }))
    const dialog = screen.getByRole("dialog")
    await userEvent.type(within(dialog).getByLabelText("Name"), "Example Supplier")
    await userEvent.type(within(dialog).getByLabelText("Email"), "supply@example.com")
    await userEvent.click(within(dialog).getByRole("button", { name: "Add supplier item" }))
    await userEvent.selectOptions(within(dialog).getByLabelText("Catalog item"), "item-1")
    await userEvent.type(within(dialog).getByLabelText("Last price (USD)"), "3.75")
    await userEvent.type(within(dialog).getByLabelText("Supplier SKU"), "EX-1")
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(body).toMatchObject({ name: "Example Supplier", email: "supply@example.com", items: [{ catalog_item_id: "item-1", supplier_sku: "EX-1", last_price_minor: 375, lead_time_days: 0 }] }))
  })
  it("maps a supplier validation error to its field", async () => {
    mocks()
    server.use(http.post(`${ROOT}/suppliers`, () => HttpResponse.json({ status: 422, code: "platform.validation_error", title: "Invalid input", errors: [{ field: "body.email", code: "invalid" }] }, { status: 422 })))
    renderApp("/purchasing")
    await userEvent.click(await screen.findByRole("tab", { name: "Suppliers" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "New supplier" })).toBeEnabled())
    await userEvent.click(screen.getByRole("button", { name: "New supplier" }))
    await userEvent.type(screen.getByLabelText("Name"), "Example Supplier")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true"))
    expect(screen.getByText("Check this value.")).toBeInTheDocument()
  })
  it("preserves PO edits on a version conflict and reloads before resubmitting", async () => {
    mocks()
    let latest = po
    const versions: number[] = []
    server.use(
      http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json(latest)),
      http.patch(`${ROOT}/purchase-orders/order-1`, async ({ request }) => {
        const body = await request.json() as { version: number }
        versions.push(body.version)
        return HttpResponse.json({ status: 409, code: "purchasing.version_conflict", title: "Conflict" }, { status: 409 })
      }),
    )
    renderApp("/purchasing/purchase-orders/order-1")
    await userEvent.click(await screen.findByRole("button", { name: "Edit purchase order" }))
    await userEvent.type(screen.getByLabelText("Notes"), "My restock note")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("This record changed since you opened it")).toBeInTheDocument()
    latest = { ...po, version: 2 }
    await userEvent.click(screen.getByRole("button", { name: "Reload" }))
    expect(screen.getByLabelText("Notes")).toHaveValue("My restock note")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(versions).toEqual([1, 2]))
  })

})
