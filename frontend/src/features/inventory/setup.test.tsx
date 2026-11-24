import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { ItemStockDetail, StockLevel } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const PADS = "00000000-0000-7000-8000-0000000000d1"
const LOC = "00000000-0000-7000-8000-0000000000e1"
const SUP = "00000000-0000-7000-8000-0000000000f1"
const PO = "00000000-0000-7000-8000-0000000000f9"
const LEVEL: StockLevel = { item_id: PADS, item_name: "Brake pads (pair)", sku: "BP-1", unit: "each", stock_location_id: LOC, on_hand: "3.000", reserved: "0.000", available: "3.000", reorder_point: null, below_reorder: false }
const DETAIL: ItemStockDetail = { levels: [LEVEL], movements: [], reservations: [], rule: null }
const mv = (quantity: string) => ({ id: "m9", item_id: PADS, quantity, reason: "count_correction", occurred_at: "2026-11-24T10:00:00Z", note: null, proposal_id: null, recorded_by: null, ref_id: null, ref_type: null, stock_location_id: LOC })
const STOCK = ["inventory.stock.read", "inventory.stock.adjust", "inventory.reorder.read", "inventory.reorder.write"]

describe("inventory setup", () => {
  it("records a stock count and says when it matched", async () => {
    mockMe(meWith(...STOCK))
    const sent: unknown[] = []
    server.use(
      http.get(`*/api/v1/inventory/items/${PADS}`, () => HttpResponse.json(DETAIL)),
      http.post("*/api/v1/inventory/counts", async ({ request }) => {
        const body = (await request.json()) as { counted: string }
        sent.push(body)
        return HttpResponse.json(body.counted === "3" ? null : mv("-1.000"))
      }),
    )
    renderApp(`/inventory/${PADS}`)
    await userEvent.click(await screen.findByRole("button", { name: "Count stock" }))
    expect(screen.getByText(/The system has 3/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Record count" }))
    expect(await screen.findByText("Enter the number you counted (0 or more).")).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Counted on the shelf"), "2")
    await userEvent.type(screen.getByLabelText("Note (optional)"), "shelf check")
    await userEvent.click(screen.getByRole("button", { name: "Record count" }))
    expect(await screen.findByText("Count recorded: -1")).toBeInTheDocument()
    expect(sent).toEqual([{ item_id: PADS, stock_location_id: LOC, counted: "2", note: "shelf check" }])

    await userEvent.click(screen.getByRole("button", { name: "Count stock" }))
    await userEvent.type(screen.getByLabelText("Counted on the shelf"), "3")
    await userEvent.click(screen.getByRole("button", { name: "Record count" }))
    expect(await screen.findByText("Count matches; nothing changed")).toBeInTheDocument()
  })

  it("counts one stock location at a time when the item is stored in several", async () => {
    mockMe(meWith(...STOCK))
    const LOC2 = "00000000-0000-7000-8000-0000000000e2"
    const sent: unknown[] = []
    server.use(
      http.get(`*/api/v1/inventory/items/${PADS}`, () => HttpResponse.json({ ...DETAIL, levels: [{ ...LEVEL, on_hand: "5.000", available: "5.000" }, { ...LEVEL, stock_location_id: LOC2 }] })),
      http.post("*/api/v1/inventory/counts", async ({ request }) => {
        sent.push(await request.json())
        return HttpResponse.json(null)
      }),
    )
    renderApp(`/inventory/${PADS}`)
    await userEvent.click(await screen.findByRole("button", { name: "Count stock" }))
    expect(screen.getByText(/The system has 5;/)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText("Stock location"), "Location 2 (3 on hand)")
    expect(screen.getByText(/The system has 3;/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Counted on the shelf"), "3")
    await userEvent.click(screen.getByRole("button", { name: "Record count" }))
    expect(await screen.findByText("Count matches; nothing changed")).toBeInTheDocument()
    expect(sent).toEqual([{ item_id: PADS, stock_location_id: LOC2, counted: "3", note: null }])
  })

  it("hides the count button without the adjust permission", async () => {
    mockMe(meWith("inventory.stock.read"))
    server.use(http.get(`*/api/v1/inventory/items/${PADS}`, () => HttpResponse.json(DETAIL)))
    renderApp(`/inventory/${PADS}`)
    expect(await screen.findByText("Stock ledger")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Count stock" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Set rule" })).not.toBeInTheDocument()
  })

  it("sets a reorder rule on an item that has none and checks the levels", async () => {
    mockMe(meWith(...STOCK))
    let rule: ItemStockDetail["rule"] = null
    let sent: unknown = null
    server.use(
      http.get(`*/api/v1/inventory/items/${PADS}`, () => HttpResponse.json({ ...DETAIL, rule })),
      http.put(`*/api/v1/inventory/reorder-rules/${PADS}`, async ({ request }) => {
        sent = await request.json()
        rule = { item_id: PADS, stock_location_id: LOC, reorder_point: "2.000", target_level: "6.000", lead_time_days: 3, supplier_hint: "Parts Co" }
        return HttpResponse.json(rule)
      }),
    )
    renderApp(`/inventory/${PADS}`)
    expect(await screen.findByText(/No reorder rule yet/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Set rule" }))
    const form = within(screen.getByRole("form", { name: "Reorder rule" }))
    await userEvent.type(form.getByLabelText("Reorder at"), "6")
    await userEvent.type(form.getByLabelText("Order up to"), "2")
    expect(form.getByRole("alert")).toHaveTextContent("Order up to must be higher than reorder at.")
    expect(form.getByRole("button", { name: "Save" })).toBeDisabled()
    await userEvent.clear(form.getByLabelText("Reorder at"))
    await userEvent.type(form.getByLabelText("Reorder at"), "2")
    await userEvent.clear(form.getByLabelText("Order up to"))
    await userEvent.type(form.getByLabelText("Order up to"), "6")
    await userEvent.clear(form.getByLabelText("Lead time (days)"))
    await userEvent.type(form.getByLabelText("Lead time (days)"), "3")
    await userEvent.type(form.getByLabelText("Usual supplier (optional)"), "Parts Co")
    await userEvent.click(form.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toEqual({ reorder_point: "2", target_level: "6", lead_time_days: 3, supplier_hint: "Parts Co" }))
    expect(await screen.findByText("Reorder at 2, up to 6; lead time 3 days")).toBeInTheDocument()
    expect(screen.getByText("Usual supplier: Parts Co")).toBeInTheDocument()
  })

  it("turns an open reorder draft into a purchase order for the chosen supplier", async () => {
    mockMe(meWith(...STOCK, "purchasing.po.read", "purchasing.po.write"))
    let sent: unknown = null
    server.use(
      http.get("*/api/v1/inventory/levels", () => HttpResponse.json([LEVEL])),
      http.get("*/api/v1/inventory/reorder-suggestions", () => HttpResponse.json([])),
      http.get("*/api/v1/inventory/reorder-drafts", () => HttpResponse.json([
        { id: "d1", status: "draft", version: 4, note: null, created_at: "2026-11-20T10:00:00Z", lines: [{ item_id: PADS, quantity: "5.000", reason_text: null }] },
        { id: "d0", status: "sent_to_purchasing", version: 2, note: null, created_at: "2026-11-01T10:00:00Z", lines: [{ item_id: PADS, quantity: "9.000", reason_text: null }] },
      ])),
      http.get("*/api/v1/purchasing/suppliers", () => HttpResponse.json({ next_cursor: null, items: [
        { id: SUP, name: "Parts Co", active: true, created_at: "2026-01-01T00:00:00Z", version: 1 },
        { id: "old", name: "Gone Ltd", active: false, created_at: "2026-01-01T00:00:00Z", version: 1 },
      ] })),
      http.post("*/api/v1/purchasing/purchase-orders/from-draft", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ id: PO }, { status: 201 }) }),
      http.get(`*/api/v1/purchasing/purchase-orders/${PO}`, () => HttpResponse.json({}, { status: 404 })),
    )
    const { router } = renderApp("/inventory")
    const drafts = within(await screen.findByRole("list", { name: "Reorder drafts" }))
    expect(drafts.getAllByRole("form")).toHaveLength(1)
    expect(drafts.getByText("Brake pads (pair)").parentElement).toHaveTextContent("Brake pads (pair): 5")
    const form = within(drafts.getByRole("form", { name: "Draft with 1 item" }))
    expect(form.getByRole("button", { name: "Create purchase order" })).toBeDisabled()
    const select = form.getByLabelText("Supplier")
    expect(within(select).queryByText("Gone Ltd")).not.toBeInTheDocument()
    await userEvent.selectOptions(select, SUP)
    await userEvent.click(form.getByRole("button", { name: "Create purchase order" }))
    await waitFor(() => expect(sent).toEqual({ draft_id: "d1", supplier_id: SUP, version: 4 }))
    await waitFor(() => expect(router.state.location.pathname).toBe(`/purchasing/purchase-orders/${PO}`))
  })

  it("asks for a supplier first when there is none", async () => {
    mockMe(meWith(...STOCK, "purchasing.po.read", "purchasing.po.write"))
    server.use(
      http.get("*/api/v1/inventory/levels", () => HttpResponse.json([LEVEL])),
      http.get("*/api/v1/inventory/reorder-suggestions", () => HttpResponse.json([])),
      http.get("*/api/v1/inventory/reorder-drafts", () => HttpResponse.json([{ id: "d1", status: "draft", version: 1, note: null, created_at: "2026-11-20T10:00:00Z", lines: [{ item_id: PADS, quantity: "5.000", reason_text: null }] }])),
      http.get("*/api/v1/purchasing/suppliers", () => HttpResponse.json({ next_cursor: null, items: [] })),
    )
    renderApp("/inventory")
    expect(await screen.findByRole("link", { name: "Add a supplier" })).toHaveAttribute("href", "/purchasing?tab=suppliers")
  })

  it("shows drafts without a convert form when the user cannot create purchase orders", async () => {
    mockMe(meWith(...STOCK))
    server.use(
      http.get("*/api/v1/inventory/levels", () => HttpResponse.json([LEVEL])),
      http.get("*/api/v1/inventory/reorder-suggestions", () => HttpResponse.json([])),
      http.get("*/api/v1/inventory/reorder-drafts", () => HttpResponse.json([{ id: "d1", status: "draft", version: 1, note: null, created_at: "2026-11-20T10:00:00Z", lines: [{ item_id: PADS, quantity: "5.000", reason_text: null }] }])),
    )
    renderApp("/inventory")
    const drafts = within(await screen.findByRole("list", { name: "Reorder drafts" }))
    expect(drafts.getByText("Brake pads (pair)")).toBeInTheDocument()
    expect(drafts.queryByRole("form")).not.toBeInTheDocument()
  })
})
