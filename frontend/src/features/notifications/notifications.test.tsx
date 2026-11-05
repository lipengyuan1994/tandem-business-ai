import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import { mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

it("shows the autonomy rule explanation in an AI acted notification", async () => {
  mockMe()
  server.use(http.get("*/api/v1/notifications", () => HttpResponse.json({ unread: 1, items: [{ id: "note-1", kind: "ai_acted", title: "AI acted: Reorder example cable", body: "Approved by autonomy rule rule-1 (v3). Check the proposal for its execution result.", link: "/approvals/proposal-1", read_at: null, created_at: "2026-11-05T17:00:00Z" }] })))
  server.use(http.get("*/api/v1/ai/autonomy/activity", () => HttpResponse.json({ items: [], next_cursor: null })))
  renderApp("/ai/activity")
  await userEvent.click(await screen.findByRole("button", { name: /Notifications/ }))
  expect(await screen.findByText("AI acted: Reorder example cable")).toBeInTheDocument()
  expect(screen.getByText("Approved by autonomy rule rule-1 (v3). Check the proposal for its execution result.")).toBeInTheDocument()
})
