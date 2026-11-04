import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import type { DocumentFamily } from "./model"
import { doc, ID, mockDocument, PERMS } from "./test-support"

const CASES: [DocumentFamily, string, string, string, string][] = [
  ["quotes", "draft", "Send quote", "send", "sent"],
  ["quotes", "sent", "Accept quote", "accept", "accepted"],
  ["quotes", "sent", "Decline quote", "decline", "declined"],
  ["quotes", "sent", "Expire quote", "expire", "expired"],
  ["quotes", "accepted", "Convert to order", "convert", "draft"],
  ["orders", "draft", "Confirm order", "confirm", "confirmed"],
  ["orders", "confirmed", "Fulfil order", "fulfil", "fulfilled"],
  ["orders", "confirmed", "Cancel order", "cancel", "cancelled"],
  ["orders", "confirmed", "Create invoice", "invoice", "draft"],
  ["orders", "fulfilled", "Create invoice", "invoice", "draft"],
  ["invoices", "draft", "Issue invoice", "issue", "issued"],
  ["invoices", "issued", "Mark as paid (record only)", "mark-paid", "paid"],
  ["invoices", "issued", "Create credit note", "credit", "issued"],
]
describe("sales actions", () => {
  it.each(CASES)("%s %s: %s calls /%s with the displayed version", async (family, status, label, action, nextStatus) => {
    mockMe(meWith(...PERMS))
    let current = doc({ status })
    let sent: unknown = null
    let csrf: string | null = null
    let releaseDestination = () => {}
    const destinationLoaded = new Promise<void>((resolve) => { releaseDestination = resolve })
    server.use(
      http.get(`*/api/v1/sales/${family}/${ID}`, () => HttpResponse.json(current)),
      http.get("*/api/v1/sales/:family", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.post(`*/api/v1/sales/${family}/${ID}/${action}`, async ({ request }) => {
        sent = await request.json(); csrf = request.headers.get("X-CSRF-Token")
        current = doc({ status: nextStatus, version: 4, id: ["convert", "invoice", "credit"].includes(action) ? "new-id" : ID })
        return HttpResponse.json(current)
      }),
      http.get("*/api/v1/sales/:family/new-id", async () => { await destinationLoaded; return HttpResponse.json(current) }),
    )
    const { router, queryClient } = renderApp(`/sales/${family}/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: label }))
    if (["issue", "credit", "cancel"].includes(action)) {
      expect(sent).toBeNull()
      await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: label }))
    }
    await waitFor(() => expect(sent).toEqual({ version: 3 }))
    expect(csrf).toBe("csrf-test-token")
    if (["convert", "invoice", "credit"].includes(action)) {
      await waitFor(() => expect(router.state.location.pathname).toBe(`/sales/${action === "convert" ? "orders" : "invoices"}/new-id`))
      releaseDestination()
      await screen.findByRole("article", { name: "Printable document" })
    }
    // Receiving the POST in MSW is earlier than mutation completion (which awaits invalidated GETs).
    await waitFor(() => {
      expect(queryClient.isMutating()).toBe(0)
      expect(queryClient.isFetching({ queryKey: ["sales"] })).toBe(0)
    })
  })

  it.each<[DocumentFamily, string, string[]]>([
    ["quotes", "draft", ["Send quote"]], ["quotes", "sent", ["Accept quote", "Decline quote", "Expire quote"]],
    ["quotes", "declined", []], ["quotes", "expired", []], ["orders", "draft", ["Confirm order"]],
    ["orders", "cancelled", []], ["invoices", "draft", ["Issue invoice"]], ["invoices", "paid", []], ["invoices", "void", []],
  ])("shows only allowed transitions for %s %s", async (family, status, expected) => {
    mockMe(meWith(...PERMS)); mockDocument(doc({ status }), family)
    renderApp(`/sales/${family}/${ID}`)
    await screen.findByRole("button", { name: "Print" })
    const labels = [...new Set(CASES.map((c) => c[2]))]
    for (const label of labels) expect(!!screen.queryByRole("button", { name: label })).toBe(expected.includes(label))
    if (status !== "draft") expect(screen.queryByRole("button", { name: "Edit items" })).not.toBeInTheDocument()
  })

  it("hides repeat conversions after checking every page of target documents", async () => {
    mockMe(meWith(...PERMS)); mockDocument(doc({ status: "accepted", kind: null }), "quotes")
    const cursors: (string | null)[] = []
    server.use(http.get("*/api/v1/sales/orders", ({ request }) => {
      const u = new URL(request.url); cursors.push(u.searchParams.get("cursor"))
      expect(u.searchParams.get("customer_id")).toBe(doc().customer_id)
      return HttpResponse.json(u.searchParams.has("cursor") ? { items: [doc({ id: "order1", source_quote_id: ID })], next_cursor: null } : { items: [], next_cursor: "page2" })
    }))
    renderApp(`/sales/quotes/${ID}`)
    expect(await screen.findByRole("link", { name: "Open order or invoice" })).toHaveAttribute("href", "/sales/orders/order1")
    expect(screen.queryByRole("button", { name: "Convert to order" })).not.toBeInTheDocument()
    expect(cursors).toEqual([null, "page2"])
  })

  it("uses separate issue, payment and credit permissions", async () => {
    mockMe(meWith("sales.document.read", "sales.document.write", "sales.invoice.mark_paid"))
    mockDocument(doc({ status: "issued" }))
    renderApp(`/sales/invoices/${ID}`)
    expect(await screen.findByRole("button", { name: "Mark as paid (record only)" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Create credit note" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Issue invoice" })).not.toBeInTheDocument()
  })
  it("does not offer invoice actions on a credit note", async () => {
    mockMe(meWith(...PERMS)); mockDocument(doc({ status: "issued", kind: "credit_note" }))
    renderApp(`/sales/invoices/${ID}`)
    await screen.findByRole("button", { name: "Print" })
    expect(screen.queryByRole("button", { name: "Create credit note" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Mark as paid (record only)" })).not.toBeInTheDocument()
  })
})
