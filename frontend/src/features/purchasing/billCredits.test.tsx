import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { BILL_PAY, BILL_READ, BILL_VOID, BILL_WRITE, billLineAmount, type Bill, type POBilling } from "./billModel"
import { READ, WRITE, type PO, type Supplier } from "./model"

// Synthetic data only.
const ROOT = "*/api/v1/purchasing"
const supplier: Supplier = { active: true, id: "supplier-1", name: "Example Parts", email: null, phone: null, notes: null, version: 1, created_at: "2026-10-28T12:00:00Z", items: [] }
const po: PO = {
  id: "order-1", supplier_id: supplier.id, supplier_name: supplier.name, currency: "USD", status: "partially_received", number: "PO-2026-0001", approved_date: "2026-10-01",
  notes: null, total_minor: 12500, source_draft_id: null, source_quote_id: null, sent_at: null, expected_delivery_date: null, last_followup_at: null, version: 3, created_at: "2026-10-01T12:00:00Z",
  lines: [
    { id: "po-line-1", catalog_item_id: "item-1", name: "Brake pads", position: 0, quantity: "10.000", received_quantity: "4.000", unit_cost_minor: 1200, line_total_minor: 12000 },
    { id: "po-line-2", catalog_item_id: "item-2", name: "Shop rags", position: 1, quantity: "1.000", received_quantity: "0.000", unit_cost_minor: 500, line_total_minor: 500 },
  ],
  receipts: [],
}
const billing: POBilling = { purchase_order_id: po.id, currency: "USD", ordered_minor: 12500, received_minor: 4800, billed_minor: 2400, unbilled_received_minor: 2400, remaining_received_lines: [{ purchase_order_line_id: "po-line-1", quantity: "2.000" }] }
const bill: Bill = {
  id: "bill-1", supplier_id: supplier.id, supplier_name: supplier.name, purchase_order_id: null, purchase_order_number: null, supplier_reference: "INV-77",
  bill_date: "2026-10-02", due_date: null, currency: "USD", total_minor: 10000, amount_paid_minor: 0, amount_credited_minor: 0, amount_refunded_minor: 0,
  balance_minor: 10000, status: "open", notes: null, voided_at: null, version: 2, created_at: "2026-10-02T12:00:00Z", payments: [], lines: [], credits: [],
}

function mocks(current: Bill, ...permissions: string[]) {
  mockMe(meWith(READ, WRITE, BILL_READ, ...permissions))
  server.use(
    http.get(`${ROOT}/purchase-orders`, () => HttpResponse.json({ items: [po], next_cursor: null })),
    http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json(po)),
    http.get(`${ROOT}/purchase-orders/order-1/billing`, () => HttpResponse.json(billing)),
    http.get(`${ROOT}/suppliers`, () => HttpResponse.json({ items: [supplier], next_cursor: null })),
    http.get(`${ROOT}/bills`, () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get(`${ROOT}/bills/bill-1`, () => HttpResponse.json(current)),
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD" })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [], next_cursor: null })),
  )
}

describe("itemised supplier bills", () => {
  it("works out a line's amount as the server does", () => {
    expect(billLineAmount("2.5", 333)).toBe(833) // 832.5 rounds half up
    expect(billLineAmount("0.001", 499)).toBe(0)
    expect(billLineAmount("0", 100)).toBeNull()
    expect(billLineAmount("1.0005", 100)).toBeNull()
  })

  it("fills only received and unbilled lines, and sends them instead of a total", async () => {
    mocks(bill, BILL_WRITE)
    let body: unknown
    server.use(http.post(`${ROOT}/bills`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...bill, id: "bill-2" }, { status: 201 }) }))
    renderApp("/purchasing/purchase-orders/order-1")
    await userEvent.click(await screen.findByRole("button", { name: "Record bill" }))
    const dialog = screen.getByRole("dialog", { name: "Record bill" })
    await userEvent.click(await within(dialog).findByRole("button", { name: "Fill from purchase order" }))
    expect(within(dialog).getByLabelText("Quantity of line 1")).toHaveValue("2") // two of the four received units are already billed
    expect(within(dialog).queryByLabelText("Quantity of line 2")).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText("Total (USD)")).not.toBeInTheDocument()
    expect(within(dialog).getByText("Total from lines: $24.00")).toBeInTheDocument()
    await userEvent.clear(within(dialog).getByLabelText("Unit cost of line 1 (USD)"))
    await userEvent.type(within(dialog).getByLabelText("Unit cost of line 1 (USD)"), "12.5")
    expect(within(dialog).getByText("Total from lines: $25.00")).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole("button", { name: "Add line" }))
    expect(within(dialog).getByText("Enter a description.")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Record bill" })).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText("Description of line 2"), "Freight")
    await userEvent.type(within(dialog).getByLabelText("Unit cost of line 2 (USD)"), "7.25")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record bill" }))
    await waitFor(() => expect(body).toMatchObject({
      purchase_order_id: "order-1", total_minor: null, match_received: true,
      lines: [
        { description: "Brake pads", quantity: "2", unit_cost_minor: 1250, catalog_item_id: "item-1", purchase_order_line_id: "po-line-1" },
        { description: "Freight", quantity: "1", unit_cost_minor: 725, catalog_item_id: null, purchase_order_line_id: null },
      ],
    }))
  })

  it("drops the order line links when the supplier changes", async () => {
    mocks(bill, BILL_WRITE)
    const other: Supplier = { ...supplier, id: "supplier-2", name: "Other Supplies" }
    let body: unknown
    server.use(
      http.get(`${ROOT}/suppliers`, () => HttpResponse.json({ items: [supplier, other], next_cursor: null })),
      http.post(`${ROOT}/bills`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...bill, id: "bill-2" }, { status: 201 }) }),
    )
    renderApp("/purchasing?tab=bills")
    await userEvent.click(await screen.findByRole("button", { name: "Record bill" }))
    const dialog = screen.getByRole("dialog", { name: "Record bill" })
    await userEvent.selectOptions(within(dialog).getByLabelText("Supplier"), await within(dialog).findByRole("option", { name: "Example Parts" }))
    await userEvent.selectOptions(within(dialog).getByLabelText("Purchase order"), await within(dialog).findByRole("option", { name: "PO-2026-0001" }))
    await userEvent.click(await within(dialog).findByRole("button", { name: "Fill from purchase order" }))
    await userEvent.selectOptions(within(dialog).getByLabelText("Supplier"), "Other Supplies")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record bill" }))
    await waitFor(() => expect(body).toMatchObject({
      supplier_id: "supplier-2", purchase_order_id: null,
      lines: [
        { description: "Brake pads", catalog_item_id: "item-1", purchase_order_line_id: null },
      ],
    }))
  })

  it("asks for manual entry when earlier bills do not allocate quantities", async () => {
    mocks(bill, BILL_WRITE)
    let body: unknown
    server.use(
      http.get(`${ROOT}/purchase-orders/order-1/billing`, () => HttpResponse.json({ ...billing, remaining_received_lines: null })),
      http.post(`${ROOT}/bills`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ ...bill, id: "bill-2" }, { status: 201 })
      }),
    )
    renderApp("/purchasing/purchase-orders/order-1")
    await userEvent.click(await screen.findByRole("button", { name: "Record bill" }))
    const dialog = screen.getByRole("dialog", { name: "Record bill" })
    expect(await within(dialog).findByText("Earlier bill lines cannot be matched", { exact: false })).toBeInTheDocument()
    expect(await within(dialog).findByRole("button", { name: "Fill from purchase order" })).toBeDisabled()
    const total = within(dialog).getByLabelText("Total (USD)")
    expect(total).toHaveValue("")
    expect(within(dialog).getByRole("button", { name: "Record bill" })).toBeDisabled()
    await userEvent.type(total, "75")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record bill" }))
    await waitFor(() => expect(body).toMatchObject({ purchase_order_id: "order-1", total_minor: 7500 }))
    expect(body).toHaveProperty("match_received", false)
  })

  it("shows a bill's lines, and editing back to no lines sends a total", async () => {
    const itemised = { ...bill, total_minor: 2400, balance_minor: 2400, lines: [{ id: "l1", position: 0, description: "Rotors", quantity: "2.000", unit_cost_minor: 1200, amount_minor: 2400, catalog_item_id: null, purchase_order_line_id: null }] }
    mocks(itemised, BILL_WRITE)
    let body: unknown
    server.use(http.patch(`${ROOT}/bills/bill-1`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...itemised, version: 3 }) }))
    renderApp("/purchasing/bills/bill-1")
    const lines = await screen.findByRole("region", { name: "Lines" })
    expect(within(lines).getByText("Rotors")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Edit bill" }))
    const dialog = screen.getByRole("dialog", { name: "Edit bill" })
    expect(within(dialog).getByLabelText("Quantity of line 1")).toHaveValue("2")
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove line 1" }))
    await userEvent.clear(within(dialog).getByLabelText("Total (USD)"))
    await userEvent.type(within(dialog).getByLabelText("Total (USD)"), "30")
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(body).toMatchObject({ version: 2, lines: null, total_minor: 3000 }))
  })
})

describe("supplier credits", () => {
  it("takes a credit off an unpaid bill's balance, with no refund", async () => {
    mocks(bill, BILL_WRITE)
    let body: unknown
    let key: string | null = null
    server.use(http.post(`${ROOT}/bills/bill-1/credits`, async ({ request }) => {
      body = await request.json(); key = request.headers.get("Idempotency-Key")
      return HttpResponse.json({ ...bill, version: 3 }, { status: 201 })
    }))
    renderApp("/purchasing/bills/bill-1")
    await userEvent.click(await screen.findByRole("button", { name: "Record supplier credit" }))
    const dialog = screen.getByRole("dialog", { name: "Record supplier credit" })
    const amount = within(dialog).getByLabelText("Credit amount (USD)")
    expect(amount).toHaveValue("100.00")
    await userEvent.clear(amount); await userEvent.type(amount, "100.01")
    expect(within(dialog).getByText("The credit can't be more than what is left to credit, $100.00.")).toBeInTheDocument()
    await userEvent.clear(amount); await userEvent.type(amount, "25")
    expect(within(dialog).queryByLabelText("Received on")).not.toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText("Supplier's credit number (optional)"), " CR-1 ")
    await userEvent.type(within(dialog).getByLabelText("Reason (optional)"), "Two damaged units")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record supplier credit" }))
    await waitFor(() => expect(body).toMatchObject({ version: 2, amount_minor: 2500, supplier_reference: "CR-1", reason: "Two damaged units", refund: null }))
    expect(key).toBeTruthy()
  })

  it("asks how the supplier paid back a credit on a paid bill", async () => {
    const paid = { ...bill, status: "paid" as const, amount_paid_minor: 10000, balance_minor: 0 }
    mocks(paid, BILL_WRITE, BILL_PAY)
    let body: unknown
    server.use(http.post(`${ROOT}/bills/bill-1/credits`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...paid, version: 3 }, { status: 201 }) }))
    renderApp("/purchasing/bills/bill-1")
    expect(await screen.findByRole("button", { name: "Record supplier credit" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Record payment" })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Record supplier credit" }))
    const dialog = screen.getByRole("dialog", { name: "Record supplier credit" })
    const amount = within(dialog).getByLabelText("Credit amount (USD)")
    await userEvent.clear(amount); await userEvent.type(amount, "30")
    expect(within(dialog).getByText("The bill was already paid, so the supplier paid back $30.00. Record how.")).toBeInTheDocument()
    await userEvent.selectOptions(within(dialog).getByLabelText("Method"), "check")
    await userEvent.type(within(dialog).getByLabelText("Reference (optional)"), "CHK-5")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record supplier credit" }))
    await waitFor(() => expect(body).toMatchObject({ amount_minor: 3000, refund: { method: "check", reference: "CHK-5" } }))
  })

  it("lists credits with what was paid back, and an admin can void one", async () => {
    const credited = {
      ...bill, status: "paid" as const, amount_paid_minor: 10000, amount_credited_minor: 3000, amount_refunded_minor: 3000, balance_minor: 0, version: 5,
      credits: [{ id: "credit-1", credit_date: "2026-10-20", amount_minor: 3000, supplier_reference: "CR-1", reason: "Returned", refund_minor: 3000, refund_received_on: "2026-10-21", refund_method: "check" as const, refund_reference: "CHK-5", voided_at: null, created_at: "2026-10-20T12:00:00Z" }],
    }
    mocks(credited, BILL_WRITE, BILL_VOID)
    let body: unknown
    server.use(http.post(`${ROOT}/bills/bill-1/credits/credit-1/void`, async ({ request }) => { body = await request.json(); return HttpResponse.json({ ...credited, version: 6 }) }))
    renderApp("/purchasing/bills/bill-1")
    const section = await screen.findByRole("region", { name: "Supplier credits" })
    expect(section).toHaveTextContent("on 2026-10-21 (Check), CHK-5")
    expect(screen.getByText("Paid back by supplier")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Edit bill" })).not.toBeInTheDocument()
    await userEvent.click(within(section).getByRole("button", { name: "Void credit" }))
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Void credit" }))
    await waitFor(() => expect(body).toEqual({ version: 5 }))
  })
})
