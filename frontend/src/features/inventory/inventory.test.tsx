import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { ItemStockDetail, StockLevel } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { withRunningBalance } from "./hooks"

const PADS = "00000000-0000-7000-8000-0000000000d1"
const LOC = "00000000-0000-7000-8000-0000000000e1"
const LEVEL: StockLevel = { item_id: PADS, item_name: "Brake pads (pair)", sku: "BP-1", unit: "each", stock_location_id: LOC, on_hand: "3.000", reserved: "2.000", available: "1.000", reorder_point: "2.000", below_reorder: true }
const mv = (id: string, quantity: string, reason: string, at: string) => ({ id, item_id: PADS, quantity, reason, occurred_at: at, note: null, proposal_id: null, recorded_by: null, ref_id: null, ref_type: null, stock_location_id: LOC })
const DETAIL: ItemStockDetail = {
  levels: [LEVEL],
  movements: [mv("m2", "-1.000", "adjustment_loss", "2026-10-20T10:00:00Z"), mv("m1", "4.000", "receipt", "2026-10-01T10:00:00Z")],
  reservations: [{ id: "r1", item_id: PADS, quantity: "1.000", ref_type: "appointment", ref_id: "a1", status: "active", created_at: "2026-11-05T17:00:00Z" }],
  rule: { item_id: PADS, stock_location_id: LOC, reorder_point: "2.000", target_level: "6.000", lead_time_days: 3, supplier_hint: null },
}
const PERMS = ["inventory.stock.read", "inventory.stock.adjust", "inventory.reorder.read", "inventory.reorder.write", "catalog.item.read", "catalog.item.write"]

describe("inventory", () => {
  it("computes the running balance oldest to newest", () => {
    expect(withRunningBalance(DETAIL.movements, 3).map((m) => [m.id, m.balance])).toEqual([["m1", 4], ["m2", 3]])
  })

  it("anchors balances to on-hand when older movements are not in the list (more than 100 movements)", () => {
    const newest = DETAIL.movements.slice(0, 1) // only the -1 adjustment is returned; 4 received earlier is omitted
    expect(withRunningBalance(newest, 3).map((m) => [m.id, m.balance])).toEqual([["m2", 3]])
  })

  it("shows on hand, reserved and available, highlights low stock and creates a reorder draft", async () => {
    mockMe(meWith(...PERMS))
    let draft: unknown = null
    server.use(
      http.get("*/api/v1/inventory/levels", () => HttpResponse.json([LEVEL])),
      http.get("*/api/v1/inventory/reorder-suggestions", () => HttpResponse.json([
        { item_id: PADS, item_name: "Brake pads (pair)", available: "1.000", reorder_point: "2.000", target_level: "6.000", suggested: "5.000", explanation: "Available 1 is below 2; order up to 6." },
      ])),
      http.post("*/api/v1/inventory/reorder-drafts", async ({ request }) => { draft = await request.json(); return HttpResponse.json({ id: "d1", status: "draft", lines: [], note: null, created_at: "2026-11-05T17:00:00Z" }, { status: 201 }) }),
    )
    renderApp("/inventory")
    const table = await screen.findByRole("table")
    const row = within(table).getByText("Brake pads (pair)").closest("tr")!
    expect(within(row).getAllByRole("cell").slice(1, 4).map((c) => c.textContent)).toEqual(["1 (below reorder point)", "3", "2"])
    expect(within(row).getByText("1").closest("[data-below]")).toHaveAttribute("data-below", "true")
    expect(screen.getByText("Available 1 is below 2; order up to 6.")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Create reorder draft" }))
    await waitFor(() => expect(draft).toEqual({ lines: [{ item_id: PADS, quantity: "5.000", reason_text: "Available 1 is below 2; order up to 6." }], note: null }))
  })

  it("shows the ledger with running balance and requires a reason to adjust; losses are sent negative", async () => {
    mockMe(meWith(...PERMS))
    let sent: unknown = null
    server.use(
      http.get(`*/api/v1/inventory/items/${PADS}`, () => HttpResponse.json(DETAIL)),
      http.post("*/api/v1/inventory/movements", async ({ request }) => { sent = await request.json(); return HttpResponse.json(mv("m3", "-2.000", "adjustment_loss", "2026-11-05T17:00:00Z"), { status: 201 }) }),
    )
    renderApp(`/inventory/${PADS}`)
    const ledger = within(await screen.findByRole("table"))
    const rows = ledger.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").slice(1).map((c) => c.textContent))
    expect(rows).toEqual([["Lost / damaged", "-1", "3"], ["Received", "+4", "4"]])
    expect(screen.getByRole("link", { name: "appointment" })).toHaveAttribute("href", "/schedule?appointment=a1")

    await userEvent.click(screen.getByRole("button", { name: "Adjust stock" }))
    const qty = screen.getByLabelText("Change (e.g. 2 or -1)")
    await userEvent.clear(qty)
    await userEvent.type(qty, "2")
    await userEvent.click(screen.getByRole("button", { name: "Record change" }))
    expect(await screen.findByText("Choose a reason.")).toBeInTheDocument()
    expect(sent).toBeNull()
    await userEvent.selectOptions(screen.getByLabelText("Reason"), "adjustment_loss")
    await userEvent.click(screen.getByRole("button", { name: "Record change" }))
    await waitFor(() => expect(sent).toEqual({ item_id: PADS, quantity: "-2", reason: "adjustment_loss", note: null }))
  })

  it("lists services and products in separate tabs", async () => {
    mockMe(meWith(...PERMS))
    const kinds: (string | null)[] = []
    server.use(http.get("*/api/v1/catalog/items", ({ request }) => {
      const kind = new URL(request.url).searchParams.get("kind")
      kinds.push(kind)
      const base = { sku: null, description: null, unit: "each", currency: "USD", buffer_minutes: 0, is_bookable_online: true, status: "active", version: 1 }
      return HttpResponse.json({ next_cursor: null, items: kind === "service"
        ? [{ ...base, id: "s1", kind: "service", name: "Brake service", price_minor: 8900, duration_minutes: 90, resource_kind: "staff", is_stock_tracked: false }]
        : [{ ...base, id: "p1", kind: "product", name: "Brake pads (pair)", price_minor: null, duration_minutes: null, resource_kind: null, is_stock_tracked: true }] })
    }))
    renderApp("/catalog")
    expect(await screen.findByText("Brake service")).toBeInTheDocument()
    expect(screen.getByText("$89.00")).toBeInTheDocument()
    expect(screen.getByText("90 min")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("tab", { name: "Products" }))
    expect(await screen.findByText("Brake pads (pair)")).toBeInTheDocument()
    expect(screen.getByText("On request")).toBeInTheDocument()
    expect(kinds).toEqual(["service", "product"])
  })
})
