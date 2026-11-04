import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it, vi } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { doc, ID, mockDocument, mockPickers, PERMS } from "./test-support"

describe("sales documents", () => {
  it.each(["Quotes", "Orders", "Invoices"])("lists and filters %s on the server, with keyboard-reachable document links", async (tab) => {
    mockMe(meWith(...PERMS))
    const filters: (string | null)[] = []
    server.use(http.get("*/api/v1/sales/:family", ({ request }) => {
      filters.push(new URL(request.url).searchParams.get("status"))
      return HttpResponse.json({ items: [doc()], next_cursor: null })
    }))
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("tab", { name: tab }))
    expect(await screen.findByRole("link", { name: "Draft" })).toHaveAttribute("href", `/sales/${tab.toLowerCase()}/${ID}`)
    const row = screen.getByRole("link", { name: "Draft" }).closest("tr")!
    expect(within(row).getByText("Ada Example")).toBeInTheDocument()
    expect(within(row).getByText("$96.34")).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText("Status"), "draft")
    await waitFor(() => expect(filters).toContain("draft"))
  })

  it("keeps stored totals while editing and displays the server's saved totals", async () => {
    mockMe(meWith(...PERMS)); mockPickers()
    let current = doc()
    let sent: unknown = null
    server.use(
      http.get(`*/api/v1/sales/invoices/${ID}`, () => HttpResponse.json(current)),
      http.patch(`*/api/v1/sales/invoices/${ID}`, async ({ request }) => {
        sent = await request.json()
        current = doc({ version: 4, total_minor: 20001, subtotal_minor: 18001, tax_minor: 2000 })
        return HttpResponse.json(current)
      }),
    )
    renderApp(`/sales/invoices/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit items" }))
    const editor = screen.getByRole("dialog")
    await userEvent.clear(within(editor).getByLabelText("Quantity"))
    await userEvent.type(within(editor).getByLabelText("Quantity"), "2")
    expect(screen.getAllByText("$96.34").length).toBeGreaterThan(0)
    expect(screen.queryByText("$192.68")).not.toBeInTheDocument()
    await userEvent.click(within(editor).getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toMatchObject({ version: 3, lines: [{ name: "Brake service", quantity: "2", unit_price_minor: 8900, tax_rate: "0.0825" }] }))
    expect(await screen.findByText("$200.01")).toBeInTheDocument()
  })

  it("preserves typed lines on a conflict and requires Reload before using the new version", async () => {
    mockMe(meWith(...PERMS)); mockDocument(); mockPickers()
    const versions: number[] = []
    server.use(http.patch(`*/api/v1/sales/invoices/${ID}`, async ({ request }) => {
      const body = await request.json() as { version: number }
      versions.push(body.version)
      return HttpResponse.json({ status: 409, code: "sales.version_conflict", title: "Conflict" }, { status: 409 })
    }))
    renderApp(`/sales/invoices/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit items" }))
    await userEvent.clear(screen.getByLabelText("Description"))
    await userEvent.type(screen.getByLabelText("Description"), "My changes")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("This record changed since you opened it")).toBeInTheDocument()
    mockDocument(doc({ version: 4 }))
    await userEvent.click(screen.getByRole("button", { name: "Reload" }))
    expect(screen.getByLabelText("Description")).toHaveValue("My changes")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(versions).toEqual([3, 4]))
  })

  it("keeps an issued invoice read-only and renders printable snapshot totals", async () => {
    mockMe(meWith(...PERMS)); mockDocument(doc({ status: "issued", number: "INV-2026-0001", issue_date: "2026-10-27" }))
    const print = vi.spyOn(window, "print").mockImplementation(() => {})
    renderApp(`/sales/invoices/${ID}`)
    expect(await screen.findByText("An issued invoice cannot be changed. Create a credit note to cancel it.")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Edit items" })).not.toBeInTheDocument()
    const printable = screen.getByRole("article", { name: "Printable document" })
    expect(within(printable).getByText("Example Cycle Works")).toBeInTheDocument()
    expect(within(printable).getByText("12 Example Lane")).toBeInTheDocument()
    expect(within(printable).getByText("Subtotal", { selector: "dt" }).nextElementSibling).toHaveTextContent("$89.00")
    expect(within(printable).getByText("Tax", { selector: "dt" }).nextElementSibling).toHaveTextContent("$7.34")
    expect(within(printable).getByText("Total", { selector: "dt" }).nextElementSibling).toHaveTextContent("$96.34")
    await userEvent.click(screen.getByRole("button", { name: "Print" }))
    expect(print).toHaveBeenCalledOnce(); print.mockRestore()
  })

  it("hides write actions for an auditor", async () => {
    mockMe(meWith("sales.document.read")); mockDocument()
    renderApp(`/sales/invoices/${ID}`)
    expect(await screen.findByRole("button", { name: "Print" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Edit items" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Issue invoice" })).not.toBeInTheDocument()
  })

  it("links to the inclusive invoice CSV date range and rejects reversed ranges", async () => {
    mockMe(meWith("sales.document.read"))
    server.use(http.get("*/api/v1/sales/:family", () => HttpResponse.json({ items: [], next_cursor: null })))
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("tab", { name: "Invoices" }))
    await userEvent.type(screen.getByLabelText("From date"), "2026-10-01")
    await userEvent.type(screen.getByLabelText("To date"), "2026-10-31")
    expect(screen.getByRole("link", { name: "Export CSV" })).toHaveAttribute("href", "/api/v1/sales/invoices/export.csv?from=2026-10-01&to=2026-10-31")
    expect(screen.getByRole("link", { name: "Export CSV" })).toHaveAttribute("download")
    await userEvent.clear(screen.getByLabelText("To date"))
    await userEvent.type(screen.getByLabelText("To date"), "2026-09-30")
    expect(screen.queryByRole("link", { name: "Export CSV" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "New invoice" })).not.toBeInTheDocument()
  })

  it("shows list loading, empty, error and retry states", async () => {
    mockMe(meWith("sales.document.read"))
    let release: () => void = () => {}
    const pending = new Promise<void>((resolve) => { release = resolve })
    let failed = false
    server.use(http.get("*/api/v1/sales/quotes", async () => {
      await pending
      return failed ? HttpResponse.json({ status: 503, code: "test.unavailable", title: "Service unavailable" }, { status: 503 }) : HttpResponse.json({ items: [], next_cursor: null })
    }))
    const { queryClient } = renderApp("/sales")
    expect(await screen.findByLabelText("Loading…")).toHaveAttribute("aria-busy", "true")
    release()
    expect(await screen.findByText("No documents found.")).toBeInTheDocument()
    failed = true
    await queryClient.invalidateQueries({ queryKey: ["sales"] })
    expect(await screen.findByText("Service unavailable")).toBeInTheDocument()
    failed = false
    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByText("No documents found.")).toBeInTheDocument()
  })

  it("loads the next cursor page", async () => {
    mockMe(meWith("sales.document.read"))
    server.use(http.get("*/api/v1/sales/quotes", ({ request }) => HttpResponse.json(new URL(request.url).searchParams.has("cursor")
      ? { items: [doc({ id: "quote2", number: "QUO-2026-0002" })], next_cursor: null }
      : { items: [doc()], next_cursor: "next-page" })))
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }))
    expect(await screen.findByRole("link", { name: "QUO-2026-0002" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Draft" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument()
  })

  it("hides navigation and denies a direct visit without sales read permission", async () => {
    mockMe(meWith())
    renderApp("/sales")
    expect(await screen.findByText("You do not have access to sales.")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Sales" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "New quote" })).not.toBeInTheDocument()
  })
})
