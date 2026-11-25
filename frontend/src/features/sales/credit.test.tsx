import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { SalesDocument, SalesLine } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { creditableLines, creditAmount, quantityMilli } from "./credit"
import { doc, ID, PERMS } from "./test-support"

// Synthetic data only.
const NOTE_ID = "00000000-0000-7000-8000-0000000000e1"
const line = (over: Partial<SalesLine>): SalesLine => ({ id: "line1", catalog_item_id: null, name: "Brake service", quantity: "1.000", unit_price_minor: 8900, tax_rate: "0.0825", line_subtotal_minor: 8900, line_tax_minor: 734, line_total_minor: 9634, position: 0, credited_quantity: "0.000", credited_subtotal_minor: 0, credited_tax_minor: 0, ...over })
// 3 × 3.33 at 7.25%: subtotal 9.99, tax 0.72.
const PADS = line({ id: "line2", name: "Brake pads", quantity: "3.000", unit_price_minor: 333, tax_rate: "0.0725", line_subtotal_minor: 999, line_tax_minor: 72, line_total_minor: 1071, position: 1 })
const ISSUED: Partial<SalesDocument> = {
  status: "issued", number: "INV-2026-0007", kind: "invoice", subtotal_minor: 9899, tax_minor: 806, total_minor: 10705,
  amount_paid_minor: 0, amount_credited_minor: 0, amount_refunded_minor: 0, balance_minor: 10705, payment_status: "unpaid",
  payments: [], credit_notes: [], refunds: [], lines: [line({}), PADS],
}

function serve(initial: SalesDocument) {
  let current = initial
  const posted: unknown[] = []
  server.use(
    http.get(`*/api/v1/sales/invoices/${ID}`, () => HttpResponse.json(current)),
    http.get(`*/api/v1/sales/invoices/${NOTE_ID}`, () => HttpResponse.json(doc({ id: NOTE_ID, kind: "credit_note", status: "issued", number: "CN-2026-0001", credit_of_invoice_id: ID }))),
    http.get("*/api/v1/sales/:family", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.post(`*/api/v1/sales/invoices/${ID}/credit`, async ({ request }) => {
      posted.push(await request.json())
      current = { ...current, version: current.version + 1 }
      return HttpResponse.json(doc({ id: NOTE_ID, kind: "credit_note", status: "issued", number: "CN-2026-0001", credit_of_invoice_id: ID }), { status: 201 })
    }),
  )
  return posted
}

describe("crediting an invoice", () => {
  it("works out a partial credit as the server does, never more than is left of the line", () => {
    const [pads] = creditableLines(doc({ ...ISSUED, lines: [PADS] }))
    expect(creditAmount(pads!, quantityMilli("1")!)).toBe(357) // 3.33 + 0.24
    // After two units were credited (6.66 + 0.48), the last one takes exactly what is left: 3.33 + 0.24.
    const [last] = creditableLines(doc({ ...ISSUED, lines: [{ ...PADS, credited_quantity: "2.000", credited_subtotal_minor: 666, credited_tax_minor: 48 }] }))
    expect(last!.leftMilli).toBe(1000n)
    expect(creditAmount(last!, 1000n)).toBe(357)
  })

  it("credits part of an unpaid invoice against its balance, with no refund", async () => {
    mockMe(meWith(...PERMS))
    const posted = serve(doc(ISSUED))
    const { router } = renderApp(`/sales/invoices/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Create credit note" }))
    const dialog = screen.getByRole("dialog", { name: "Credit invoice" })
    expect(within(dialog).getByLabelText("Quantity of Brake pads")).toHaveValue("3")
    await userEvent.clear(within(dialog).getByLabelText("Quantity of Brake service"))
    await userEvent.type(within(dialog).getByLabelText("Quantity of Brake service"), "0")
    await userEvent.clear(within(dialog).getByLabelText("Quantity of Brake pads"))
    await userEvent.type(within(dialog).getByLabelText("Quantity of Brake pads"), "1")
    expect(within(dialog).getAllByText("$3.57")).toHaveLength(3) // the line, the credit note total and what comes off the balance
    expect(within(dialog).queryByLabelText("Refund date")).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue credit note" }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toEqual({ version: 3, lines: [{ line_id: "line2", quantity: "1" }], refund: null })
    await waitFor(() => expect(router.state.location.pathname).toBe(`/sales/invoices/${NOTE_ID}`))
  })

  it("refunds what was paid beyond the balance when crediting a paid invoice", async () => {
    mockMe(meWith(...PERMS))
    const posted = serve(doc({ ...ISSUED, status: "paid", amount_paid_minor: 10705, balance_minor: 0, payment_status: "paid" }))
    renderApp(`/sales/invoices/${ID}`)
    expect(screen.queryByRole("button", { name: "Mark fully paid" })).not.toBeInTheDocument()
    await userEvent.click(await screen.findByRole("button", { name: "Create credit note" }))
    const dialog = screen.getByRole("dialog", { name: "Credit invoice" })
    await userEvent.clear(within(dialog).getByLabelText("Quantity of Brake pads"))
    expect(within(dialog).getByText("Record how $96.34 was paid back to the customer.")).toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText("Quantity of Brake pads"), "4")
    expect(within(dialog).getByText("Only 3 is left to credit.")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Issue credit note" })).toBeDisabled()
    await userEvent.clear(within(dialog).getByLabelText("Quantity of Brake pads"))
    await userEvent.type(within(dialog).getByLabelText("Quantity of Brake pads"), "3")
    await userEvent.selectOptions(within(dialog).getByLabelText("Method"), "card")
    await userEvent.type(within(dialog).getByLabelText("Reference (optional)"), " RF-1 ")
    await userEvent.click(within(dialog).getByRole("button", { name: "Issue credit note" }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toMatchObject({
      version: 3,
      lines: [{ line_id: "line1", quantity: "1" }, { line_id: "line2", quantity: "3" }],
      refund: { method: "card", reference: "RF-1" },
    })
  })

  it("lists the invoice's credit notes and refunds, and stops offering a credit once everything is credited", async () => {
    mockMe(meWith(...PERMS))
    serve(doc({
      ...ISSUED, status: "void", amount_paid_minor: 10705, amount_credited_minor: 10705, amount_refunded_minor: 10705, balance_minor: 0, payment_status: "paid",
      lines: [line({ credited_quantity: "1.000", credited_subtotal_minor: 8900, credited_tax_minor: 734 }), { ...PADS, credited_quantity: "3.000", credited_subtotal_minor: 999, credited_tax_minor: 72 }],
      credit_notes: [{ id: NOTE_ID, number: "CN-2026-0001", issue_date: "2026-11-20", total_minor: 10705 }],
      refunds: [{ id: "refund1", credit_note_id: NOTE_ID, amount_minor: 10705, paid_on: "2026-11-21", method: "bank_transfer", reference: "RF-1", created_at: "2026-11-21T12:00:00Z" }],
    }))
    renderApp(`/sales/invoices/${ID}`)
    const notes = await screen.findByRole("region", { name: "Credit notes" })
    expect(within(notes).getByRole("link", { name: "CN-2026-0001" })).toHaveAttribute("href", `/sales/invoices/${NOTE_ID}`)
    expect(notes).toHaveTextContent("Refunded on 2026-11-21: $107.05 (Bank transfer, RF-1)")
    expect(screen.getByText("Refunded to customer")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Create credit note" })).not.toBeInTheDocument()
  })
})
