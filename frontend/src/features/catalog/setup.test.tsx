import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const base = { sku: null, description: null, unit: "each", currency: "USD", buffer_minutes: 0, is_bookable_online: true, status: "active", version: 3 }
const SERVICE = { ...base, id: "s1", kind: "service", name: "Brake service", price_minor: 8900, duration_minutes: 90, resource_kind: "staff", is_stock_tracked: false, required_resources: [] }
const PADS = { ...base, id: "p1", kind: "product", name: "Brake pads (pair)", price_minor: null, duration_minutes: null, resource_kind: null, is_stock_tracked: true }
const FLUID = { ...PADS, id: "p2", name: "Brake fluid" }
const CHARGE = { ...PADS, id: "p3", name: "Diagnostic fee", is_stock_tracked: false }
const OLD = { ...PADS, id: "p4", name: "Old pads", status: "archived" }

function catalog() {
  server.use(
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD" })),
    http.get("*/api/v1/catalog/items", ({ request }) => HttpResponse.json({ next_cursor: null, items: new URL(request.url).searchParams.get("kind") === "service" ? [SERVICE] : [PADS, FLUID, CHARGE, OLD] })),
  )
}

describe("catalog setup", () => {
  it("edits the parts a service uses: only active stock-tracked products, quantities checked", async () => {
    mockMe(meWith("catalog.item.read", "catalog.item.write"))
    catalog()
    let sent: unknown = null
    server.use(
      http.get("*/api/v1/catalog/items/s1/parts", () => HttpResponse.json([{ item_id: "p1", item_name: "Brake pads (pair)", quantity: "1.000" }])),
      http.put("*/api/v1/catalog/items/s1/parts", async ({ request }) => { sent = await request.json(); return HttpResponse.json([]) }),
    )
    renderApp("/catalog")
    await userEvent.click(await screen.findByRole("button", { name: "Parts used by Brake service" }))
    const dialog = within(await screen.findByRole("dialog"))
    expect(await dialog.findByLabelText("Quantity of Brake pads (pair)")).toHaveValue("1")
    const add = dialog.getByLabelText("Add a product")
    expect(within(add).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose a product", "Brake fluid"])
    await userEvent.selectOptions(add, "p2")
    await userEvent.click(dialog.getByRole("button", { name: "Add" }))
    const fluid = dialog.getByLabelText("Quantity of Brake fluid")
    await userEvent.clear(fluid)
    await userEvent.type(fluid, "0")
    expect(dialog.getByRole("alert")).toHaveTextContent("Each quantity must be above 0")
    expect(dialog.getByRole("button", { name: "Save" })).toBeDisabled()
    await userEvent.clear(fluid)
    await userEvent.type(fluid, "0.25")
    await userEvent.click(dialog.getByRole("button", { name: "Remove Brake pads (pair)" }))
    await userEvent.click(dialog.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toEqual([{ item_id: "p2", quantity: "0.25" }]))
  })

  it("archives an item after confirming, and explains when a service still uses it", async () => {
    mockMe(meWith("catalog.item.read", "catalog.item.write"))
    catalog()
    const archived: unknown[] = []
    server.use(
      http.post("*/api/v1/catalog/items/:id/archive", async ({ params, request }) => {
        archived.push({ id: params.id, ...(await request.json() as object) })
        return params.id === "p1"
          ? HttpResponse.json({ type: "about:blank", title: "Conflict", status: 409, code: "catalog.item_in_use", detail: "required by service 'Brake service'" }, { status: 409, headers: { "content-type": "application/problem+json" } })
          : HttpResponse.json({ ...FLUID, status: "archived", version: 4 })
      }),
    )
    renderApp("/catalog")
    await userEvent.click(await screen.findByRole("tab", { name: "Products" }))
    await userEvent.click(await screen.findByRole("button", { name: "Edit Brake pads (pair)" }))
    await userEvent.click(screen.getByRole("button", { name: "Archive" }))
    const confirm = within(await screen.findByRole("dialog", { name: "Archive Brake pads (pair)?" }))
    await userEvent.click(confirm.getByRole("button", { name: "Archive" }))
    expect(await screen.findByText(/An active service still uses this product as a part/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }))
    await userEvent.click(screen.getByRole("button", { name: "Edit Brake fluid" }))
    await userEvent.click(screen.getByRole("button", { name: "Archive" }))
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Archive Brake fluid?" })).getByRole("button", { name: "Archive" }))
    expect(await screen.findByText("Brake fluid archived")).toBeInTheDocument()
    expect(archived).toEqual([{ id: "p1", version: 3 }, { id: "p2", version: 3 }])
  })

  it("hides parts and archive without write permission", async () => {
    mockMe(meWith("catalog.item.read"))
    catalog()
    renderApp("/catalog")
    expect(await screen.findByText("Brake service")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Parts used by Brake service" })).not.toBeInTheDocument()
  })
})
