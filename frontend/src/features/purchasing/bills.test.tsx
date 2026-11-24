import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { BILL_PAY, BILL_READ, BILL_VOID, BILL_WRITE, type Bill, type PayablesReport, type POBilling } from "./billModel"
import { READ, WRITE, type PO, type Supplier } from "./model"

// Synthetic data only.
const ROOT = "*/api/v1/purchasing"
const supplier: Supplier = { active: true, id: "supplier-1", name: "Example Parts", email: null, phone: null, notes: null, version: 1, created_at: "2026-10-28T12:00:00Z", items: [] }
const po: PO = { id: "order-1", supplier_id: supplier.id, supplier_name: supplier.name, currency: "USD", status: "received", number: "PO-2026-0001", approved_date: "2026-10-01", notes: null, total_minor: 10000, source_draft_id: null, source_quote_id: null, sent_at: null, expected_delivery_date: null, last_followup_at: null, version: 3, created_at: "2026-10-01T12:00:00Z", lines: [], receipts: [] }
const bill: Bill = {
  id: "bill-1", supplier_id: supplier.id, supplier_name: supplier.name, purchase_order_id: po.id, purchase_order_number: po.number, supplier_reference: "INV-77",
  bill_date: "2026-10-02", due_date: "2026-11-01", currency: "USD", total_minor: 10000, amount_paid_minor: 2500, balance_minor: 7500, status: "partially_paid",
  notes: null, voided_at: null, version: 2, created_at: "2026-10-02T12:00:00Z",
  payments: [{ id: "pay-1", amount_minor: 2500, paid_on: "2026-10-05", method: "check", reference: "1001", voided_at: null, created_at: "2026-10-05T12:00:00Z" }],
}
const billing: POBilling = { purchase_order_id: po.id, currency: "USD", ordered_minor: 10000, received_minor: 10000, billed_minor: 0, unbilled_received_minor: 10000 }

function mocks(...permissions: string[]) {
  mockMe(meWith(READ, WRITE, ...permissions))
  server.use(
    http.get(`${ROOT}/purchase-orders`, () => HttpResponse.json({ items: [po], next_cursor: null })),
    http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json(po)),
    http.get(`${ROOT}/purchase-orders/order-1/billing`, () => HttpResponse.json(billing)),
    http.get(`${ROOT}/suppliers`, () => HttpResponse.json({ items: [supplier], next_cursor: null })),
    http.get(`${ROOT}/bills`, () => HttpResponse.json({ items: [bill], next_cursor: null })),
    http.get(`${ROOT}/bills/bill-1`, () => HttpResponse.json(bill)),
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD" })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [], next_cursor: null })),
  )
}

describe("supplier bills", () => {
  it("hides the bill tabs without the bill permission", async () => {
    mocks(); renderApp("/purchasing")
    expect(await screen.findByRole("tab", { name: "Purchase orders" })).toBeInTheDocument()
    expect(screen.queryByRole("tab", { name: "Supplier bills" })).not.toBeInTheDocument()
    expect(screen.queryByRole("tab", { name: "Payables aging" })).not.toBeInTheDocument()
  })

  it("lists unpaid bills by default with their balance", async () => {
    mocks(BILL_READ)
    let query = ""
    server.use(http.get(`${ROOT}/bills`, ({ request }) => { query = new URL(request.url).search; return HttpResponse.json({ items: [bill], next_cursor: null }) }))
    renderApp("/purchasing?tab=bills")
    expect(await screen.findByRole("link", { name: "INV-77" })).toHaveAttribute("href", "/purchasing/bills/bill-1")
    expect(screen.getByText("$75.00")).toBeInTheDocument()
    expect(new URLSearchParams(query).getAll("status")).toEqual(["open", "partially_paid"])
    expect(screen.queryByRole("button", { name: "Record bill" })).not.toBeInTheDocument()
  })

  it("records a payment no larger than the balance, with an idempotency key", async () => {
    mocks(BILL_READ, BILL_PAY)
    let body: unknown
    let key: string | null = null
    server.use(http.post(`${ROOT}/bills/bill-1/payments`, async ({ request }) => {
      body = await request.json(); key = request.headers.get("Idempotency-Key")
      return HttpResponse.json({ ...bill, amount_paid_minor: 7500, balance_minor: 2500, version: 3 }, { status: 201 })
    }))
    renderApp("/purchasing/bills/bill-1")
    await userEvent.click(await screen.findByRole("button", { name: "Record payment" }))
    const dialog = screen.getByRole("dialog", { name: "Record payment" })
    const amount = within(dialog).getByLabelText("Amount (USD)")
    expect(amount).toHaveValue("75.00")
    await userEvent.clear(amount); await userEvent.type(amount, "80")
    expect(within(dialog).getByText("The payment can't be more than the balance due, $75.00.")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Record payment" })).toBeDisabled()
    await userEvent.clear(amount); await userEvent.type(amount, "50")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record payment" }))
    await waitFor(() => expect(body).toMatchObject({ version: 2, amount_minor: 5000, method: "bank_transfer", reference: null }))
    expect(key).toBeTruthy()
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  })

  it("offers void and edit only where the bill's state and role allow", async () => {
    mocks(BILL_READ, BILL_WRITE, BILL_PAY, BILL_VOID)
    renderApp("/purchasing/bills/bill-1")
    expect(await screen.findByRole("button", { name: "Record payment" })).toBeInTheDocument()
    // Partly paid: no edit, no void of the bill itself, but the payment can be voided.
    expect(screen.queryByRole("button", { name: "Edit bill" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Void bill" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Void payment" })).toBeInTheDocument()
  })

  it("saves an edit with the version the form opened with, so a concurrent edit is not overwritten", async () => {
    mocks(BILL_READ, BILL_WRITE)
    const open = { ...bill, status: "open" as const, amount_paid_minor: 0, balance_minor: 10000, payments: [], version: 4 }
    const bodies: unknown[] = []
    server.use(
      http.get(`${ROOT}/bills/bill-1`, () => HttpResponse.json(open)),
      http.patch(`${ROOT}/bills/bill-1`, async ({ request }) => {
        const body = (await request.json()) as { version: number }
        bodies.push(body)
        return body.version === 5 ? HttpResponse.json({ ...open, version: 6 }) : HttpResponse.json({ code: "purchasing.version_conflict", status: 409, title: "Conflict" }, { status: 409 })
      }),
    )
    const { queryClient } = renderApp("/purchasing/bills/bill-1")
    await userEvent.click(await screen.findByRole("button", { name: "Edit bill" }))
    const dialog = screen.getByRole("dialog", { name: "Edit bill" })
    // Someone else edits the bill (version 5) and the page refetches while this dialog is still open.
    server.use(http.get(`${ROOT}/bills/bill-1`, () => HttpResponse.json({ ...open, total_minor: 12000, version: 5 })))
    await queryClient.refetchQueries({ queryKey: ["purchasing", "bill", "bill-1"] })
    await userEvent.clear(within(dialog).getByLabelText("Total (USD)")); await userEvent.type(within(dialog).getByLabelText("Total (USD)"), "90")
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toMatchObject({ version: 4, total_minor: 9000 })
    // The stale edit is refused and the reload prompt appears instead of a silent overwrite.
    expect(await screen.findByRole("dialog", { name: "This record changed since you opened it" })).toBeInTheDocument()
  })

  it("starts a bill from a received order at the value not yet billed", async () => {
    mocks(BILL_READ, BILL_WRITE)
    let body: unknown
    server.use(
      http.get(`${ROOT}/bills`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.post(`${ROOT}/bills`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...bill, id: "bill-2" }, { status: 201 }) }),
    )
    renderApp("/purchasing/purchase-orders/order-1")
    expect(await screen.findByText("No bills recorded for this order yet.")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Record bill" }))
    const dialog = screen.getByRole("dialog", { name: "Record bill" })
    await waitFor(() => expect(within(dialog).getByLabelText("Total (USD)")).toHaveValue("100.00"))
    expect(within(dialog).getByLabelText("Supplier")).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText("Supplier's bill number"), " INV-88 ")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record bill" }))
    await waitFor(() => expect(body).toMatchObject({ supplier_id: "supplier-1", purchase_order_id: "order-1", supplier_reference: "INV-88", total_minor: 10000, due_date: null }))
    expect(body).not.toHaveProperty("version")
  })

  it("shows payables aging per currency", async () => {
    mocks(BILL_READ)
    const row = { supplier_id: supplier.id, supplier_name: supplier.name, currency: "USD", bill_count: 1, current_minor: 0, days_1_30_minor: 7500, days_31_60_minor: 0, days_61_90_minor: 0, days_over_90_minor: 0, total_minor: 7500 }
    const report: PayablesReport = { as_of: "2026-11-24", suppliers: [row], totals: [{ currency: "USD", bill_count: 1, current_minor: 0, days_1_30_minor: 7500, days_31_60_minor: 0, days_61_90_minor: 0, days_over_90_minor: 0, total_minor: 7500 }] }
    server.use(http.get(`${ROOT}/payables/aging`, () => HttpResponse.json(report)))
    renderApp("/purchasing?tab=payables")
    const table = await screen.findByRole("table", { name: "Amounts owed in USD" })
    const cells = within(within(table).getAllByRole("row")[1]!).getAllByRole("cell")
    expect(cells.map((c) => c.textContent)).toEqual(["Example Parts", "1", "$0.00", "$75.00", "$0.00", "$0.00", "$0.00", "$75.00"])
  })
})
