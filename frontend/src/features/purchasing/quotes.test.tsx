import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { toast } from "sonner"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { Schemas } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { READ, WRITE } from "./model"

const ROOT = "*/api/v1/purchasing"
const request: Schemas["QuoteRequestOut"] = {
  id: "request-1", status: "sent", needed_by: "2099-11-20", reply_by: "2099-11-10", message: null, version: 1, created_by: null, created_at: "2099-11-01T12:00:00Z",
  lines: [{ item_id: "item-1", name: "Example pads", quantity: "2" }],
  suppliers: [{ id: "invite-1", supplier_id: "supplier-1", supplier_name: "Example Parts", status: "invited", sent_at: null, responded_at: null, last_followup_at: null }], quotes: [],
}
const score: Schemas["QuoteScore"] = { quote_id: "quote-1", quote_version: 3, supplier_id: "supplier-1", supplier_name: "Example Parts", total_minor: 2500, currency: "USD", lead_time_days: 3, delivery_date: "2099-11-04", days_late: 0, price_score: 1, lead_score: 1, reliability: 0.8, orders_count: 2, score: 0.97 }
function setup(write = true) {
  mockMe(meWith(READ, ...(write ? [WRITE] : []), "catalog.item.read"))
  server.use(
    http.get(`${ROOT}/purchase-orders`, () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get(`${ROOT}/quote-requests`, () => HttpResponse.json({ items: [request], next_cursor: null })),
    http.get(`${ROOT}/quote-requests/request-1`, () => HttpResponse.json(request)),
    http.get(`${ROOT}/quote-requests/request-1/comparison`, () => HttpResponse.json({ quote_request_id: request.id, as_of: "2099-11-01", ranked: [score, { ...score, quote_id: "quote-2", supplier_name: "Example Backup", score: 0.88, price_score: 0.85 }], ineligible: [{ quote_id: "quote-3", supplier_id: "supplier-1", reasons: ["expired"] }] })),
    http.get(`${ROOT}/suppliers`, () => HttpResponse.json({ items: Array.from({ length: 6 }, (_, i) => ({ id: `supplier-${i + 1}`, name: i === 0 ? "Example Parts" : `Example ${i + 1}`, email: `supplier${i}@example.com`, items: [{ catalog_item_id: "item-1" }] })).concat([{ id: "unrelated", name: "Unrelated Supplier", email: "other@example.com", items: [{ catalog_item_id: "other" }] }]), next_cursor: null })),
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD" })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [{ id: "item-1", name: "Example pads", kind: "product", status: "active", is_stock_tracked: true }], next_cursor: null })),
  )
}
const LINK = `${ROOT}/quote-requests/request-1/invites/supplier-1/link`
const SECRET_URL = "https://example.com/q/capability-for-the-test"
function clipboard(writeText: () => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
}
const proposal = () => HttpResponse.json({ id: "proposal-1", status: "pending_review", current_revision_id: "revision-1" }, { status: 201 })

describe("quote procurement", () => {
  it("validates lines, dates and 1–5 matching suppliers and sends a proposal payload", async () => {
    setup()
    let body: unknown
    server.use(http.post(`${ROOT}/quote-requests`, async ({ request }) => { body = await request.json(); return proposal() }))
    renderApp("/purchasing?tab=quotes")
    await userEvent.click(await screen.findByRole("button", { name: "Request quotes" }))
    const dialog = screen.getByRole("dialog", { name: "Request quotes" })
    const send = within(dialog).getByRole("button", { name: "Request quotes" })
    expect(send).toBeDisabled()
    await userEvent.selectOptions(await within(dialog).findByLabelText("Catalog item"), "item-1")
    expect(within(dialog).queryByLabelText("Unrelated Supplier")).not.toBeInTheDocument()
    await userEvent.clear(within(dialog).getByLabelText("Quantity"))
    await userEvent.type(within(dialog).getByLabelText("Quantity"), "2.5")
    await userEvent.type(within(dialog).getByLabelText("Needed by"), "2099-11-20")
    await userEvent.type(within(dialog).getByLabelText("Reply by"), "2099-11-21")
    await userEvent.click(within(dialog).getByLabelText("Example Parts"))
    expect(send).toBeDisabled()
    await userEvent.clear(within(dialog).getByLabelText("Reply by"))
    await userEvent.type(within(dialog).getByLabelText("Reply by"), "2099-11-10")
    for (let i = 2; i <= 5; i++) await userEvent.click(within(dialog).getByLabelText(`Example ${i}`))
    expect(within(dialog).getByLabelText("Example 6")).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText("Message (optional)"), "Please quote this stock")
    await userEvent.click(send)
    await waitFor(() => expect(body).toEqual({ lines: [{ item_id: "item-1", quantity: "2.5" }], supplier_ids: ["supplier-1", "supplier-2", "supplier-3", "supplier-4", "supplier-5"], needed_by: "2099-11-20", reply_by: "2099-11-10", message: "Please quote this stock" }))
    expect(await within(dialog).findByRole("link", { name: "Review approval" })).toHaveAttribute("href", "/approvals/proposal-1")
  })
  it("shows response counts and links to the request", async () => {
    setup(); renderApp("/purchasing?tab=quotes")
    expect(await screen.findByText("0 of 1")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Example pads" })).toHaveAttribute("href", "/purchasing/quote-requests/request-1")
  })
  it("renders server ranks, formula and components, then proposes the exact quote version", async () => {
    setup()
    let body: unknown
    server.use(http.post(`${ROOT}/purchase-orders/from-quote`, async ({ request }) => { body = await request.json(); return proposal() }))
    renderApp("/purchasing/quote-requests/request-1")
    const table = await screen.findByRole("table", { name: "Quote comparison" })
    const rows = within(table).getAllByRole("row")
    expect(rows[1]).toHaveTextContent("1Example Parts")
    expect(rows[2]).toHaveTextContent("2Example Backup")
    for (const heading of ["Price component", "Delivery component", "Reliability", "Score"]) expect(within(table).getByRole("columnheader", { name: heading })).toBeInTheDocument()
    expect(rows[1]).toHaveTextContent("0.970")
    expect(screen.getByText(/60% price/)).toBeInTheDocument()
    expect(within(rows[1]!).getByText(/Default 80%/)).toBeInTheDocument()
    expect(screen.getByText(/Quote expired/)).toBeInTheDocument()
    await userEvent.click(within(rows[1]!).getByRole("button", { name: "Create purchase order from this quote" }))
    await waitFor(() => expect(body).toEqual({ quote_id: "quote-1", quote_version: 3 }))
    expect(await screen.findByRole("link", { name: "Review approval" })).toBeInTheDocument()
  })
  it("manually enters decimal prices as minor units", async () => {
    setup()
    let body: unknown
    server.use(http.post(`${ROOT}/quote-requests/request-1/quotes`, async ({ request }) => { body = await request.json(); return proposal() }))
    renderApp("/purchasing/quote-requests/request-1")
    await userEvent.click(await screen.findByRole("button", { name: "Enter quote" }))
    const dialog = screen.getByRole("dialog", { name: "Enter quote" })
    await userEvent.type(await within(dialog).findByLabelText("Unit price (USD) · Example pads"), "12.50")
    await userEvent.type(within(dialog).getByLabelText("Lead time (days)"), "3")
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(body).toMatchObject({ source: "staff", quote_request_id: "request-1", supplier_id: "supplier-1", currency: "USD", lines: [{ item_id: "item-1", unit_price_minor: 1250, quantity_available: null }], lead_time_days: 3 }))
  })
  it("proposes a supplier follow-up with the request binding", async () => {
    setup()
    let body: unknown
    server.use(http.post(`${ROOT}/supplier-followups`, async ({ request }) => { body = await request.json(); return proposal() }))
    renderApp("/purchasing/quote-requests/request-1")
    await userEvent.click(await screen.findByRole("button", { name: "Send follow-up" }))
    const dialog = screen.getByRole("dialog", { name: "Send follow-up" })
    await userEvent.type(within(dialog).getByLabelText("Message"), "Please reply with a quote")
    await userEvent.click(within(dialog).getByRole("button", { name: "Send follow-up" }))
    await waitFor(() => expect(body).toEqual({ supplier_id: "supplier-1", about: { quote_request_id: "request-1" }, body: "Please reply with a quote" }))
  })
  it("keeps procurement read-only without write permission", async () => {
    setup(false); renderApp("/purchasing/quote-requests/request-1")
    await screen.findByRole("table", { name: "Quote comparison" })
    expect(screen.queryByRole("button", { name: "Enter quote" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Create purchase order from this quote" })).not.toBeInTheDocument()
  })
  describe("copying a pending supplier's link", () => {
    afterEach(() => { Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true }); vi.restoreAllMocks() })

    it("copies the link to the clipboard and says so without showing it", async () => {
      setup()
      const writeText = vi.fn(() => Promise.resolve())
      clipboard(writeText)
      const done = vi.spyOn(toast, "success")
      server.use(http.post(LINK, () => HttpResponse.json({ url: SECRET_URL })))
      renderApp("/purchasing/quote-requests/request-1")
      await userEvent.click(await screen.findByRole("button", { name: "Copy link for Example Parts" }))
      await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(SECRET_URL))
      expect(done).toHaveBeenCalledWith(expect.stringContaining("Link copied"))
      expect(document.body).not.toHaveTextContent("capability-for-the-test") // the link is a password: never on the page
    })
    it("shows the link to copy by hand only when the browser refuses to copy", async () => {
      setup()
      clipboard(() => Promise.reject(new Error("denied")))
      server.use(http.post(LINK, () => HttpResponse.json({ url: SECRET_URL })))
      renderApp("/purchasing/quote-requests/request-1")
      await userEvent.click(await screen.findByRole("button", { name: "Copy link for Example Parts" }))
      const dialog = await screen.findByRole("dialog", { name: "Link for Example Parts" })
      expect(within(dialog).getByRole("textbox", { name: "Quote link" })).toHaveValue(SECRET_URL)
      expect(within(dialog).getByRole("textbox", { name: "Quote link" })).toHaveAttribute("readonly")
      await userEvent.keyboard("{Escape}")
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
      expect(document.body).not.toHaveTextContent("capability-for-the-test")
    })
    it("explains a refusal instead of copying anything", async () => {
      setup()
      const writeText = vi.fn(() => Promise.resolve())
      clipboard(writeText)
      const failed = vi.spyOn(toast, "error")
      server.use(http.post(LINK, () => HttpResponse.json({ code: "purchasing.invite_not_unanswered", status: 409, title: "Conflict" }, { status: 409 })))
      renderApp("/purchasing/quote-requests/request-1")
      await userEvent.click(await screen.findByRole("button", { name: "Copy link for Example Parts" }))
      await waitFor(() => expect(failed).toHaveBeenCalledWith("This supplier has already answered, or the invitation has expired."))
      expect(writeText).not.toHaveBeenCalled()
    })
    it("is offered only to staff who may write, for suppliers that have not answered", async () => {
      setup(false)
      const view = renderApp("/purchasing/quote-requests/request-1")
      await screen.findByRole("table", { name: "Quote comparison" })
      expect(screen.queryByRole("button", { name: /^Copy link/ })).not.toBeInTheDocument()
      view.unmount()
      setup()
      server.use(http.get(`${ROOT}/quote-requests/request-1`, () => HttpResponse.json({ ...request, suppliers: [{ ...request.suppliers[0], status: "responded" }] })))
      renderApp("/purchasing/quote-requests/request-1")
      await screen.findByRole("button", { name: "Enter quote" })
      expect(screen.queryByRole("button", { name: /^Copy link/ })).not.toBeInTheDocument()
    })
  })
  it("loads late orders from the dedicated endpoint", async () => {
    setup()
    server.use(http.get(`${ROOT}/late-purchase-orders`, () => HttpResponse.json([{ id: "late-1", number: "PO-2099-0001", supplier_id: "supplier-1", supplier_name: "Example Parts", expected_delivery_date: "2099-10-20", days_late: 3, last_followup_at: null, open_lines: [{ item_id: "item-1", name: "Example pads", quantity: "2" }] }])))
    renderApp("/purchasing?late=true")
    await waitFor(() => expect(screen.getByRole("link", { name: "PO-2099-0001" })).toBeInTheDocument())
    expect(screen.getByText("3 days late")).toBeInTheDocument()
  })
})
