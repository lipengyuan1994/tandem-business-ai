import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { SalesDocument } from "@/api/types"
import { doc, ID, PERMS } from "@/features/sales/test-support"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { LINK_SHARE, type InvoiceOnlinePayments } from "./api"

// Synthetic data only.
const ISSUED: Partial<SalesDocument> = { status: "issued", number: "INV-2026-0007", kind: "invoice", amount_paid_minor: 0, balance_minor: 9634, payment_status: "unpaid", payments: [] }
const URL1 = "https://app.example.com/pay/first.token"
const URL2 = "https://app.example.com/pay/second.token"
const READY: InvoiceOnlinePayments = { invoice_id: ID, available: true, unavailable_reason: null, link_url: null, link_created_at: null, link_last_opened_at: null, mode: "test", requests: [] }

function serve(summary: InvoiceOnlinePayments, invoice: Partial<SalesDocument> = ISSUED) {
  let current = summary
  const posts: string[] = []
  server.use(
    http.get(`*/api/v1/sales/invoices/${ID}`, () => HttpResponse.json(doc(invoice))),
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD", timezone: "America/Denver" })),
    http.get(`*/api/v1/payments/invoices/${ID}`, () => HttpResponse.json(current)),
    http.post(`*/api/v1/payments/invoices/${ID}/link`, () => {
      posts.push("link"); current = { ...current, link_url: current.link_url ?? URL1, link_created_at: "2026-11-27T15:00:00Z" }
      return HttpResponse.json({ url: current.link_url, created_at: current.link_created_at })
    }),
    http.post(`*/api/v1/payments/invoices/${ID}/link/reset`, () => {
      posts.push("reset"); current = { ...current, link_url: URL2 }
      return HttpResponse.json({ url: URL2, created_at: "2026-11-27T16:00:00Z" })
    }),
  )
  return posts
}

afterEach(() => vi.restoreAllMocks())

describe("invoice pay link", () => {
  it("copies a new pay link, then replaces it after a warning", async () => {
    mockMe(meWith(...PERMS, LINK_SHARE))
    const posts = serve(READY)
    const user = userEvent.setup()
    const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue()
    renderApp(`/sales/invoices/${ID}`)
    const card = await screen.findByRole("region", { name: "Online payment" })
    expect(within(card).getByText("Test mode: no real money moves.")).toBeInTheDocument()
    await user.click(within(card).getByRole("button", { name: "Copy pay link" }))
    await waitFor(() => expect(clipboard).toHaveBeenCalledWith(URL1))
    expect(await within(card).findByLabelText("Pay link")).toHaveValue(URL1)

    await user.click(within(card).getByRole("button", { name: "Replace link" }))
    const dialog = await screen.findByRole("dialog", { name: "Replace the pay link?" })
    await user.click(within(dialog).getByRole("button", { name: "Replace link" }))
    await waitFor(() => expect(within(card).getByLabelText("Pay link")).toHaveValue(URL2))
    expect(posts).toEqual(["link", "reset"])
  })

  it("points to settings while Stripe isn't set up", async () => {
    mockMe(meWith(...PERMS, LINK_SHARE))
    serve({ ...READY, available: false, unavailable_reason: "not_connected", mode: null })
    renderApp(`/sales/invoices/${ID}`)
    const card = await screen.findByRole("region", { name: "Online payment" })
    expect(card).toHaveTextContent("Set up online payments in Settings > Payments")
    expect(within(card).queryByRole("button")).not.toBeInTheDocument()
  })

  it("lists attempts and flags money that came in after the invoice was settled", async () => {
    mockMe(meWith(...PERMS, LINK_SHARE))
    serve({
      ...READY, available: false, unavailable_reason: "settled",
      requests: [{ id: "r1", status: "paid", amount_minor: 9634, currency: "USD", method: "card", applied_minor: 0, surplus_minor: 9634, created_at: "2026-11-27T15:00:00Z", paid_at: "2026-11-27T15:05:00Z" }],
    }, { ...ISSUED, status: "paid", balance_minor: 0, payment_status: "paid" })
    renderApp(`/sales/invoices/${ID}`)
    const card = await screen.findByRole("region", { name: "Online payment" })
    expect(within(card).getByText("Paid")).toBeInTheDocument()
    expect(within(card).getByRole("note")).toHaveTextContent("$96.34 came in after the invoice was already settled")
    expect(card).not.toHaveTextContent("Set up online payments")
  })

  it("stays hidden from people who can't share pay links", async () => {
    mockMe(meWith(...PERMS))
    serve(READY)
    renderApp(`/sales/invoices/${ID}`)
    expect(await screen.findByRole("heading", { name: "INV-2026-0007" })).toBeInTheDocument()
    expect(screen.queryByRole("region", { name: "Online payment" })).not.toBeInTheDocument()
  })
})
