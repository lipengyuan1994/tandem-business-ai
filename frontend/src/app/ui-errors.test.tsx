import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

it.each([
  ["/inbox", "/conversations", "conversations.thread.read", "No conversations"],
  ["/inbox/thread-1", "/conversations/thread-1", "conversations.thread.read", "Conversation not found"],
  ["/marketing", "/marketing/campaigns", "marketing.campaign.read", "No campaigns"],
  ["/marketing/campaign-1", "/marketing/campaigns/campaign-1", "marketing.campaign.read", "Campaign not found"],
  ["/customers/customer-1", "/crm/customers/customer-1", "crm.customer.read", "Customer not found"],
  ["/inventory/item-1", "/inventory/items/item-1", "inventory.stock.read", "Item not found"],
  ["/settings/business", "/org/business", "org.business.read", "No business"],
] as const)("shows a retryable failure on %s instead of empty or missing data", async (path, endpoint, permission, empty) => {
  mockMe(meWith(permission))
  let requests = 0
  server.use(
    http.get(`*/api/v1${endpoint}`, () => { requests++; return HttpResponse.json({ title: "Temporarily unavailable", status: 503, code: "unavailable" }, { status: 503 }) }),
    http.get("*/api/v1/conversations", () => HttpResponse.json({ items: [], next_cursor: null })),
  )
  renderApp(path)
  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("Temporarily unavailable")
  expect(screen.queryByText(empty, { exact: false })).not.toBeInTheDocument()
  await userEvent.click(within(alert).getByRole("button", { name: "Try again" }))
  await waitFor(() => expect(requests).toBe(2))
})
