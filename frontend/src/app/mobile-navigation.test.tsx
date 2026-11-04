import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

function setup() {
  mockMe(meWith("crm.customer.read", "conversations.thread.read", "scheduling.appointment.read"))
  server.use(
    http.get("*/api/v1/crm/customers", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get("*/api/v1/dashboard/counters", () => HttpResponse.json({ approvals_i_can_decide: 2, inbox_waiting_on_staff: 3, unread_notifications: 0 })),
  )
  return renderApp("/customers")
}

describe("phone menu", () => {
  it("opens from the keyboard, marks the current page, preserves counts and permissions", async () => {
    setup()
    const menu = await screen.findByRole("button", { name: "Menu" })
    expect(menu).toHaveClass("md:hidden")
    expect(menu).toHaveAttribute("aria-expanded", "false")
    menu.focus()
    await userEvent.keyboard("{Enter}")
    const dialog = await screen.findByRole("dialog", { name: "Main" })
    expect(menu).toHaveAttribute("aria-expanded", "true")
    expect(within(dialog).getByRole("link", { name: "Customers" })).toHaveAttribute("aria-current", "page")
    expect(within(dialog).getByRole("link", { name: /Inbox/ })).toHaveTextContent("3")
    expect(within(dialog).getByRole("link", { name: /Approvals/ })).toHaveTextContent("2")
    expect(within(dialog).getByRole("link", { name: "Schedule" })).toBeInTheDocument()
    expect(within(dialog).queryByRole("link", { name: "Inventory" })).not.toBeInTheDocument()
    await userEvent.keyboard("{Escape}")
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    await waitFor(() => expect(menu).toHaveFocus())
  })

  it("closes the drawer after navigation", async () => {
    const { router } = setup()
    await userEvent.click(await screen.findByRole("button", { name: "Menu" }))
    const dialog = await screen.findByRole("dialog")
    await userEvent.click(within(dialog).getByRole("link", { name: /Approvals/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe("/approvals"))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
})
