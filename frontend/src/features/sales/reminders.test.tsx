import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { doc, ID, PERMS } from "./test-support"

describe("invoice reminders", () => {
  it("filters overdue invoices on the server and posts the staff's typed text and displayed version", async () => {
    mockMe(meWith(...PERMS))
    let sent: unknown = null
    let csrf: string | null = null
    const overdue = doc({ status: "issued", number: "INV-2026-0001", due_date: "2026-10-01" })
    server.use(
      http.get("*/api/v1/sales/:family", ({ request }) => HttpResponse.json({
        items: new URL(request.url).searchParams.get("overdue") === "true" ? [overdue] : [overdue, doc({ id: "paid", status: "paid", number: "INV-2026-0002" })], next_cursor: null,
      })),
      http.post(`*/api/v1/sales/invoices/${ID}/reminders`, async ({ request }) => {
        sent = await request.json(); csrf = request.headers.get("X-CSRF-Token")
        return HttpResponse.json({ id: "proposal-1", status: "pending_review", current_revision_id: "revision-1" }, { status: 201 })
      }),
    )
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("tab", { name: "Invoices" }))
    expect(await screen.findByRole("link", { name: "INV-2026-0002" })).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText("Status"), "overdue")
    expect(await screen.findByRole("link", { name: "INV-2026-0001" })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole("link", { name: "INV-2026-0002" })).not.toBeInTheDocument())
    await userEvent.click(await screen.findByRole("button", { name: "Send reminder" }))
    const dialog = await screen.findByRole("dialog", { name: "Send reminder" })
    const submit = within(dialog).getByRole("button", { name: "Create reminder proposal" })
    expect(submit).toBeDisabled()
    const input = within(dialog).getByLabelText("Message")
    expect(input).toHaveAttribute("maxlength", "2000")
    await userEvent.type(input, "Please pay this invoice. Thank you!")
    await userEvent.click(submit)
    expect(await screen.findByRole("link", { name: "Review reminder proposal" })).toHaveAttribute("href", "/approvals/proposal-1")
    expect(sent).toEqual({ body: "Please pay this invoice. Thank you!", version: 3 })
    expect(csrf).toBe("csrf-test-token")
  })

  it("does not show reminder actions to read-only staff", async () => {
    mockMe(meWith("sales.document.read"))
    server.use(http.get("*/api/v1/sales/:family", () => HttpResponse.json({ items: [doc({ status: "issued", number: "INV-2026-0001" })], next_cursor: null })))
    renderApp("/sales")
    await userEvent.click(await screen.findByRole("tab", { name: "Invoices" }))
    await userEvent.selectOptions(screen.getByLabelText("Status"), "overdue")
    await screen.findByRole("link", { name: "INV-2026-0001" })
    expect(screen.queryByRole("button", { name: "Send reminder" })).not.toBeInTheDocument()
  })
})
