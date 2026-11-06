import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { TEAM_AGENT } from "./team-testing"
import { mockStatus, RUN, sseResponse, THREAD } from "./testing"

function setup(managerEnabled = true) {
  mockMe(meWith("ai.assistant.use"))
  mockStatus()
  server.use(
    http.get("*/api/v1/ai/agents", () => HttpResponse.json({ items: [TEAM_AGENT, { ...TEAM_AGENT, key: "operations_manager", name: "Operations Manager", enabled: managerEnabled, status: managerEnabled ? "on" : "off", reason: managerEnabled ? null : "The Manager is off for this business. Turn it on in Team." }, { ...TEAM_AGENT, key: "finance", name: "Finance", enabled: false, status: "off" }] })),
    http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([])),
    http.post("*/api/v1/ai/copilot/threads", () => HttpResponse.json({ id: THREAD, title: "Stock", created_at: "2026-11-06T17:00:00Z", updated_at: "2026-11-06T17:00:00Z" }, { status: 201 })),
    http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json({ id: THREAD, title: "Stock", messages: [] })),
    http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
  )
}
async function panel() {
  await userEvent.click(await screen.findByRole("button", { name: "Assistant" }))
  return within(await screen.findByRole("dialog", { name: "Assistant" }))
}

describe("Team assistant", () => {
  it("offers enabled agents at @, sends the chip's agent and retains it on same-key retry", async () => {
    setup()
    const bodies: unknown[] = []
    const keys: (string | null)[] = []
    server.use(http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, async ({ request }) => {
      bodies.push(await request.json()); keys.push(request.headers.get("Idempotency-Key"))
      return bodies.length === 1 ? HttpResponse.json({ status: 503, title: "Unavailable", code: "ai.provider_unavailable" }, { status: 503 }) : sseResponse([["final", { run_id: RUN, text: "Stock reviewed." }]])
    }))
    renderApp("/approvals")
    const p = await panel()
    await userEvent.type(p.getByLabelText("Ask the assistant"), "@")
    const choices = await p.findByRole("group", { name: "Talk to an agent" })
    expect(within(choices).queryByRole("button", { name: "Finance" })).not.toBeInTheDocument()
    await userEvent.click(within(choices).getByRole("button", { name: "Inventory" }))
    expect(p.getByText("To: Inventory")).toBeInTheDocument()
    expect(p.getByLabelText("Ask the assistant")).toHaveValue("")
    await userEvent.type(p.getByLabelText("Ask the assistant"), "Check stock")
    await userEvent.click(p.getByRole("button", { name: "Ask" }))
    await waitFor(() => expect(bodies).toEqual([{ content: "Check stock", agent: "inventory" }]))
    await userEvent.click(await p.findByRole("button", { name: "Retry message" }))
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[1]).toEqual(bodies[0]); expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0])
  })

  it("removes the chip and omits agent for the Manager's default route", async () => {
    setup()
    let body: unknown
    server.use(http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, async ({ request }) => { body = await request.json(); return sseResponse([["final", { run_id: RUN, text: "Done." }]]) }))
    renderApp("/approvals")
    const p = await panel()
    await userEvent.type(p.getByLabelText("Ask the assistant"), "@Inv")
    await userEvent.click(await p.findByRole("button", { name: "Inventory" }))
    await userEvent.click(p.getByRole("button", { name: "Remove agent" }))
    expect(p.queryByText("To: Inventory")).not.toBeInTheDocument()
    await userEvent.type(p.getByLabelText("Ask the assistant"), "Check tomorrow{Enter}")
    await waitFor(() => expect(body).toEqual({ content: "Check tomorrow" }))
  })

  it("shows the backend's Manager-off explanation instead of the input", async () => {
    setup(false)
    renderApp("/approvals")
    const p = await panel()
    expect(await p.findByText("The Manager is off for this business. Turn it on in Team.")).toHaveAttribute("role", "status")
    expect(p.queryByLabelText("Ask the assistant")).not.toBeInTheDocument()
    expect(p.queryByRole("button", { name: "Ask" })).not.toBeInTheDocument()
  })
})
