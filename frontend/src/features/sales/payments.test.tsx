import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { AgingReport, SalesDocument } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { doc, ID, PERMS } from "./test-support"

const ISSUED = { status: "issued", number: "INV-2026-0007", kind: "invoice", amount_paid_minor: 0, balance_minor: 9634, payment_status: "unpaid", payments: [] } as const
const PAYMENT = { id: "pay1", amount_minor: 4000, paid_on: "2026-10-20", method: "card", reference: "AUTH-9", voided_at: null, created_at: "2026-10-20T15:00:00Z" } as const

function serve(initial: SalesDocument) {
  let current = initial
  const posted: { url: string; body: unknown }[] = []
  server.use(
    http.get(`*/api/v1/sales/invoices/${ID}`, () => HttpResponse.json(current)),
    http.get("*/api/v1/sales/:family", () => HttpResponse.json({ items: [current], next_cursor: null })),
    http.post(`*/api/v1/sales/invoices/${ID}/payments`, async ({ request }) => {
      const body = await request.json() as { amount_minor: number }
      posted.push({ url: request.url, body })
      current = doc({ ...ISSUED, version: 4, amount_paid_minor: body.amount_minor, balance_minor: 9634 - body.amount_minor, payment_status: "partially_paid", payments: [{ ...PAYMENT, amount_minor: body.amount_minor }] })
      return HttpResponse.json(current, { status: 201 })
    }),
    http.post(`*/api/v1/sales/invoices/${ID}/payments/pay1/void`, async ({ request }) => {
      posted.push({ url: request.url, body: await request.json() })
      current = doc({ ...ISSUED, version: 5, payments: [{ ...PAYMENT, voided_at: "2026-10-28T12:00:00Z" }] })
      return HttpResponse.json(current)
    }),
  )
  return posted
}

describe("invoice payments", () => {
  it("records a partial payment with the displayed version", async () => {
    mockMe(meWith(...PERMS))
    const posted = serve(doc(ISSUED))
    const { queryClient } = renderApp(`/sales/invoices/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Record payment" }))
    const dialog = await screen.findByRole("dialog", { name: "Record payment" })
    const amount = within(dialog).getByLabelText("Amount (USD)")
    expect(amount).toHaveValue("96.34")
    await userEvent.clear(amount)
    await userEvent.type(amount, "96.35")
    expect(within(dialog).getByText("The amount is more than the open balance of $96.34.")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Record payment" })).toBeDisabled()
    await userEvent.clear(amount)
    await userEvent.type(amount, "40")
    await userEvent.selectOptions(within(dialog).getByLabelText("Method"), "card")
    await userEvent.type(within(dialog).getByLabelText("Reference (optional)"), " AUTH-9 ")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record payment" }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]!.body).toMatchObject({ version: 3, amount_minor: 4000, method: "card", reference: "AUTH-9" })
    expect((posted[0]!.body as { paid_on: string }).paid_on).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    const history = await screen.findByRole("region", { name: "Payments" })
    expect(within(history).getByText("AUTH-9")).toBeInTheDocument()
    expect(await screen.findAllByText("Partially paid")).not.toHaveLength(0)
    await waitFor(() => expect(queryClient.isFetching({ queryKey: ["sales"] })).toBe(0))
  })

  it("voids a payment after confirmation; operators cannot void", async () => {
    mockMe(meWith(...PERMS))
    const posted = serve(doc({ ...ISSUED, amount_paid_minor: 4000, balance_minor: 5634, payment_status: "partially_paid", payments: [PAYMENT] }))
    const { queryClient } = renderApp(`/sales/invoices/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Void payment" }))
    expect(posted).toHaveLength(0)
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Void payment" }))
    await waitFor(() => expect(posted).toEqual([{ url: expect.stringContaining("/payments/pay1/void"), body: { version: 3 } }]))
    expect(await screen.findByText("Voided")).toBeInTheDocument()
    await waitFor(() => expect(queryClient.isFetching({ queryKey: ["sales"] })).toBe(0))
  })

  it("hides payment actions without the permissions", async () => {
    mockMe(meWith("sales.document.read"))
    serve(doc({ ...ISSUED, payments: [PAYMENT] }))
    renderApp(`/sales/invoices/${ID}`)
    expect(await screen.findByRole("region", { name: "Payments" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Record payment" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Void payment" })).not.toBeInTheDocument()
  })
})

describe("receivables aging", () => {
  it("shows open balances by customer and bucket, and asks for a chosen date", async () => {
    mockMe(meWith("sales.document.read"))
    const asked: (string | null)[] = []
    const report: AgingReport = {
      as_of: "2026-10-28",
      customers: [{ customer_id: "c1", customer_name: "Ada Example", currency: "USD", invoice_count: 2, current_minor: 1000, days_1_30_minor: 0, days_31_60_minor: 2500, days_61_90_minor: 0, days_over_90_minor: 0, total_minor: 3500 }],
      totals: [{ currency: "USD", invoice_count: 2, current_minor: 1000, days_1_30_minor: 0, days_31_60_minor: 2500, days_61_90_minor: 0, days_over_90_minor: 0, total_minor: 3500 }],
    }
    server.use(
      http.get("*/api/v1/sales/receivables/aging", ({ request }) => { asked.push(new URL(request.url).searchParams.get("as_of")); return HttpResponse.json(report) }),
      http.get("*/api/v1/sales/:family", () => HttpResponse.json({ items: [], next_cursor: null })),
    )
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("tab", { name: "Receivables" }))
    const table = await screen.findByRole("table", { name: "Amounts owed in USD" })
    const [, adaRow, totalRow] = within(table).getAllByRole("row")
    expect(within(adaRow!).getByText("Ada Example")).toBeInTheDocument()
    expect(within(adaRow!).getByText("$25.00")).toBeInTheDocument()
    expect(within(totalRow!).getAllByText("$35.00")).toHaveLength(1)
    expect(asked).toEqual([null])
    const asOf = screen.getByLabelText("As of date")
    await userEvent.type(asOf, "2026-09-30")
    await waitFor(() => expect(asked).toContain("2026-09-30"))
  })

  it("says when nothing is owed", async () => {
    mockMe(meWith("sales.document.read"))
    server.use(
      http.get("*/api/v1/sales/receivables/aging", () => HttpResponse.json({ as_of: "2026-10-28", customers: [], totals: [] })),
      http.get("*/api/v1/sales/:family", () => HttpResponse.json({ items: [], next_cursor: null })),
    )
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("tab", { name: "Receivables" }))
    expect(await screen.findByText("No open invoice balances.")).toBeInTheDocument()
  })
})
