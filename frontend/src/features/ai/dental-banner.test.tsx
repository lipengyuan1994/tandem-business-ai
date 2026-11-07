import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
const banner = "Synthetic demo data. Clinic PHI requires clinic-controlled local models."
function setup() {
  mockMe(meWith("ai.assistant.use", "conversations.thread.read", "org.settings.read", "approvals.autonomy.manage", "knowledge.article.read"))
  server.use(
    http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings: { terminology: {} }, version: 1, synthetic_demo_banner: banner })),
    http.get("*/api/v1/conversations", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get("*/api/v1/ai/autonomy/rules", () => HttpResponse.json({ items: [], paused: false })),
    http.get("*/api/v1/knowledge/articles", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get("*/api/v1/knowledge/suggestions", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get("*/api/v1/ai/status", () => HttpResponse.json({ enabled: true, policy: "hosted_vultr", budget_percent: 0 })),
    http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json({ items: [], next_cursor: null })),
  )
}
it.each(["/inbox", "/ai/team", "/ai/autonomy", "/knowledge"])("shows the backend statement on %s", async (path) => {
  setup()
  renderApp(path)
  expect(await screen.findByText(banner)).toBeInTheDocument()
})
it("shows the backend statement inside the assistant", async () => {
  setup()
  renderApp("/ai/team")
  await userEvent.click(await screen.findByRole("button", { name: "Assistant" }))
  const dialog = await screen.findByRole("dialog")
  expect(dialog).toHaveTextContent(banner)
})
