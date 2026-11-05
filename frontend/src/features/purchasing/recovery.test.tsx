import { act, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it, vi } from "vitest"
import { toast } from "sonner"
import type { Schemas } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { OVERRIDE, READ, RECEIVE, WRITE, type PO, type Supplier } from "./model"

const ROOT = "*/api/v1/purchasing"
const supplier: Supplier = { active: true, id: "supplier-1", name: "Example Parts", email: "parts@example.com", phone: null, notes: null, version: 1, created_at: "2026-10-28T12:00:00Z", items: [] }
const po: PO = { id: "order-1", supplier_id: supplier.id, supplier_name: supplier.name, currency: "USD", status: "sent", number: "PO-2026-0001", approved_date: "2026-10-28", notes: null, total_minor: 12000, source_draft_id: null, source_quote_id: null, sent_at: null, expected_delivery_date: null, last_followup_at: null, version: 3, created_at: "2026-10-28T12:00:00Z", lines: [{ id: "line-1", catalog_item_id: "item-1", name: "Example pads", quantity: "10", unit_cost_minor: 1200, line_total_minor: 12000, position: 0, received_quantity: "0" }], receipts: [] }
const delivered: PO = { ...po, version: 4, status: "partially_received", lines: po.lines!.map((line) => ({ ...line, received_quantity: "4" })), receipts: [{ id: "receipt-1", received_by: null, override_reason: null, created_at: "2026-10-28T12:30:00Z", lines: [{ po_line_id: "line-1", quantity: "4" }] }] }
interface Submission { key: string | null; body: Schemas["ReceiptIn"] }
async function submission(request: Request): Promise<Submission> { return { key: request.headers.get("Idempotency-Key"), body: await request.json() as Schemas["ReceiptIn"] } }
function setup() {
  mockMe(meWith(READ, WRITE, RECEIVE, OVERRIDE, "catalog.item.read"))
  server.use(
    http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json(po)),
    http.get(`${ROOT}/suppliers`, () => HttpResponse.json({ items: [supplier], next_cursor: null })),
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD" })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [], next_cursor: null })),
  )
}
async function enterReceipt() {
  await userEvent.click(await screen.findByRole("button", { name: "Receive goods" }))
  const dialog = screen.getByRole("dialog", { name: "Receive goods" })
  await userEvent.type(within(dialog).getByLabelText("Quantity received · Example pads"), "4")
  await userEvent.click(within(dialog).getByRole("button", { name: "Record receipt" }))
  return dialog
}

describe("purchasing recovery", () => {
  it("retries a lost receipt response with the same key, version and quantities after a background refresh", async () => {
    setup()
    const requests: Submission[] = []
    let stockAdded = 0
    let latest = po
    server.use(
      http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json(latest)),
      http.post(`${ROOT}/purchase-orders/order-1/receipts`, async ({ request }) => {
        requests.push(await submission(request))
        if (requests.length === 1) { stockAdded += 4; latest = delivered; return HttpResponse.error() }
        if (requests[1]!.key !== requests[0]!.key) stockAdded += 4
        return HttpResponse.json(delivered, { status: 201 })
      }),
    )
    const { queryClient } = renderApp("/purchasing/purchase-orders/order-1")
    const dialog = await enterReceipt()
    expect(await within(dialog).findByText("The result is unknown. Retry this receipt or reload to check recorded deliveries.")).toBeInTheDocument()
    await act(async () => { queryClient.setQueryData(["purchasing", "order", po.id], delivered) })
    expect(within(dialog).getByLabelText("Quantity received · Example pads")).toBeDisabled()
    expect(within(dialog).getByLabelText("Quantity received · Example pads")).toHaveValue("4")
    expect(within(dialog).getByRole("button", { name: "Record receipt" })).toBeDisabled()
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled()
    await userEvent.keyboard("{Escape}")
    expect(dialog).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole("button", { name: "Retry this receipt" }))
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Receive goods" })).not.toBeInTheDocument())
    expect(requests).toHaveLength(2)
    expect(requests[0]!.key).toMatch(/^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i)
    expect(requests[1]).toEqual(requests[0])
    expect(requests[1]!.body).toMatchObject({ version: 3, lines: [{ po_line_id: "line-1", quantity: "4" }] })
    expect(stockAdded).toBe(4)
  })

  it.each(["unknown", "conflict"])("reloads and reviews actual receipts before a new attempt after %s", async (outcome) => {
    setup()
    let latest = po
    const requests: Submission[] = []
    server.use(
      http.get(`${ROOT}/purchase-orders/order-1`, () => HttpResponse.json(latest)),
      http.post(`${ROOT}/purchase-orders/order-1/receipts`, async ({ request }) => {
        requests.push(await submission(request))
        latest = delivered
        if (requests.length === 1) return outcome === "unknown" ? HttpResponse.error() : HttpResponse.json({ status: 409, code: "purchasing.version_conflict", title: "Conflict" }, { status: 409 })
        return HttpResponse.json({ ...delivered, version: 5 }, { status: 201 })
      }),
    )
    renderApp("/purchasing/purchase-orders/order-1")
    const dialog = await enterReceipt()
    const reload = await within(dialog).findByRole("button", { name: "Reload recorded deliveries" })
    expect(within(dialog).getByRole("button", { name: "Record receipt" })).toBeDisabled()
    await userEvent.click(reload)
    const review = await within(dialog).findByRole("region", { name: "Recorded deliveries" })
    expect(within(review).getByText("Received 4 of 10")).toBeInTheDocument()
    expect(within(review).getByText("Example pads: 4")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Record receipt" })).toBeDisabled()
    expect(within(dialog).getByLabelText("Quantity received · Example pads")).toBeDisabled()
    await userEvent.click(within(dialog).getByRole("button", { name: "Start another receipt" }))
    const quantity = within(dialog).getByLabelText("Quantity received · Example pads")
    expect(quantity).toHaveValue("")
    expect(within(dialog).getByRole("button", { name: "Record receipt" })).toBeDisabled()
    await userEvent.type(quantity, "2")
    await userEvent.click(within(dialog).getByRole("button", { name: "Record receipt" }))
    await waitFor(() => expect(requests).toHaveLength(2))
    expect(requests[1]!.key).toBeTruthy()
    expect(requests[1]!.key).not.toBe(requests[0]!.key)
    expect(requests[1]!.body).toMatchObject({ version: 4, lines: [{ po_line_id: "line-1", quantity: "2" }] })
  })

  it("keeps the uncertain receipt and original retry intact when its reload fails", async () => {
    setup()
    let reloadFailed = false
    const requests: Submission[] = []
    server.use(
      http.get(`${ROOT}/purchase-orders/order-1`, () => reloadFailed ? HttpResponse.json({ status: 503, code: "test.reload_failed", title: "Reload failed" }, { status: 503 }) : HttpResponse.json(po)),
      http.post(`${ROOT}/purchase-orders/order-1/receipts`, async ({ request }) => {
        requests.push(await submission(request))
        return HttpResponse.error()
      }),
    )
    renderApp("/purchasing/purchase-orders/order-1")
    const dialog = await enterReceipt()
    const reload = await within(dialog).findByRole("button", { name: "Reload recorded deliveries" })
    reloadFailed = true
    await userEvent.click(reload)
    expect(await within(dialog).findByText("Could not reload deliveries. Try again before starting another receipt.")).toBeInTheDocument()
    expect(within(dialog).queryByRole("button", { name: "Start another receipt" })).not.toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Record receipt" })).toBeDisabled()
    await userEvent.click(within(dialog).getByRole("button", { name: "Retry this receipt" }))
    await waitFor(() => expect(requests).toHaveLength(2))
    expect(requests[1]).toEqual(requests[0])
  })

  it("does not treat a rejected retry as proof that an earlier uncertain receipt failed", async () => {
    setup()
    const requests: Submission[] = []
    server.use(http.post(`${ROOT}/purchase-orders/order-1/receipts`, async ({ request }) => {
      requests.push(await submission(request))
      return requests.length === 1 ? HttpResponse.error() : HttpResponse.json({ status: 403, code: "auth.permission_denied", title: "Permission denied" }, { status: 403 })
    }))
    renderApp("/purchasing/purchase-orders/order-1")
    const dialog = await enterReceipt()
    await userEvent.click(await within(dialog).findByRole("button", { name: "Retry this receipt" }))
    await within(dialog).findByText("You don't have permission to do that.")
    expect(within(dialog).getByLabelText("Quantity received · Example pads")).toBeDisabled()
    expect(within(dialog).getByRole("button", { name: "Record receipt" })).toBeDisabled()
    expect(within(dialog).getByRole("button", { name: "Reload recorded deliveries" })).toBeEnabled()
    expect(requests).toHaveLength(2)
    expect(requests[1]).toEqual(requests[0])
  })

  it("keeps supplier conflict recovery open when reload fails with cached supplier data", async () => {
    setup()
    const reportError = vi.spyOn(toast, "error")
    let failing = false
    const versions: number[] = []
    server.use(
      http.get(`${ROOT}/purchase-orders`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get(`${ROOT}/suppliers`, () => failing ? HttpResponse.json({ status: 503, code: "test.reload_failed", title: "Supplier reload failed" }, { status: 503 }) : HttpResponse.json({ items: [{ ...supplier, version: versions.length ? 2 : 1 }], next_cursor: null })),
      http.patch(`${ROOT}/suppliers/supplier-1`, async ({ request }) => {
        versions.push((await request.json() as { version: number }).version)
        return HttpResponse.json({ status: 409, code: "purchasing.version_conflict", title: "Conflict" }, { status: 409 })
      }),
    )
    const { queryClient } = renderApp("/purchasing")
    await userEvent.click(await screen.findByRole("tab", { name: "Suppliers" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled())
    await userEvent.click(screen.getByRole("button", { name: "Edit" }))
    const edit = screen.getByRole("dialog", { name: "Edit supplier" })
    await userEvent.type(within(edit).getByLabelText("Notes"), "My supplier note")
    await userEvent.click(within(edit).getByRole("button", { name: "Save" }))
    const recovery = await screen.findByRole("dialog", { name: "This record changed since you opened it" })
    failing = true
    await userEvent.click(within(recovery).getByRole("button", { name: "Reload" }))
    await waitFor(() => expect(queryClient.getQueryState(["purchasing", "suppliers"])?.status).toBe("error"))
    expect(recovery).toBeInTheDocument()
    await waitFor(() => expect(reportError).toHaveBeenCalledWith("Supplier reload failed"))
    expect(within(edit).getByRole("button", { name: "Save", hidden: true })).toBeDisabled()
    expect(within(edit).getByLabelText("Notes")).toHaveValue("My supplier note")
    failing = false
    await userEvent.click(within(recovery).getByRole("button", { name: "Reload" }))
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "This record changed since you opened it" })).not.toBeInTheDocument())
    await userEvent.click(within(edit).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(versions).toEqual([1, 2]))
    reportError.mockRestore()
  })
})
