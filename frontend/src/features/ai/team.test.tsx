import { act, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ME, meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import type { Agent } from "./team-api"
import { TEAM_ACTION, TEAM_AGENT, TEAM_NOW, teamItem } from "./team-testing"

function setup(items: Agent[] = [TEAM_AGENT], write = true) {
  mockMe(write ? meWith("org.settings.write") : ME)
  server.use(http.get("*/api/v1/ai/agents", () => HttpResponse.json({ items })))
}
const PATCH = "*/api/v1/ai/agents/inventory"

describe("AI Team", () => {
  afterEach(() => vi.unstubAllGlobals())
  it("renders every status, unavailable and Coming soon explanations, counters and filtered links", async () => {
    setup([
      TEAM_AGENT,
      { ...TEAM_AGENT, key: "finance", name: "Finance" },
      { ...TEAM_AGENT, key: "operations_manager", name: "Operations Manager" },
      { ...TEAM_AGENT, key: "front_desk", name: "Front Desk", status: "paused" },
      { ...TEAM_AGENT, key: "customer_care", name: "Customer Care", enabled: false, status: "off", reason: "No triggers yet; coming soon" },
      { ...TEAM_AGENT, key: "marketing", name: "Marketing", status: "limit_reached" },
      { ...TEAM_AGENT, key: "purchasing", name: "Purchasing", status: "unavailable", reason: "regulated: no local route for agent" },
    ], false)
    renderApp("/ai/team")
    expect(await screen.findAllByText("On")).toHaveLength(3)
    for (const text of ["Off", "Paused for everyone", "Daily limit reached", "Unavailable", "Coming soon", "Needs a local model under this business's privacy setting"]) expect(screen.getByText(text)).toBeInTheDocument()
    expect(screen.getAllByText("4 runs · 3 proposals")).toHaveLength(7)
    expect(screen.getAllByText("Done by people: 1 · By rules: 2 · Handed to a person: 1")).toHaveLength(7)
    expect(screen.getAllByText("Cost today: $0.12 / $1.00")).toHaveLength(7)
    expect(screen.getAllByRole("link", { name: /Act and notify · shadow · 10\/day/ })[0]).toHaveAttribute("href", `/ai/autonomy?action=${encodeURIComponent(TEAM_ACTION)}`)
    expect(screen.getAllByRole("link", { name: "2 waiting items" })[0]).toHaveAttribute("href", "/ai/work-queue?agent=inventory")
    expect(screen.getAllByText(/oldest .* ago/)).toHaveLength(7)
    expect(screen.getAllByText("6 Nov 2026 10:00 MST")).toHaveLength(7)
    expect(screen.queryByRole("switch")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Daily cost limit (USD)")).not.toBeInTheDocument()
  })

  it("confirms turning off and PATCHes the loaded version; turning on needs no confirmation", async () => {
    setup()
    let current = TEAM_AGENT
    const bodies: unknown[] = []
    server.use(http.get("*/api/v1/ai/agents", () => HttpResponse.json({ items: [current] })), http.patch(PATCH, async ({ request }) => {
      const body = await request.json() as { enabled: boolean; version: number }
      bodies.push(body)
      current = { ...current, enabled: body.enabled, status: body.enabled ? "on" : "off", version: current.version + 1 }
      return HttpResponse.json(current)
    }))
    renderApp("/ai/team")
    await userEvent.click(await screen.findByRole("switch", { name: "Enable Inventory" }))
    expect(bodies).toHaveLength(0)
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("Its queued work goes back to people")).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))
    expect(bodies).toHaveLength(0)
    await userEvent.click(screen.getByRole("switch", { name: "Enable Inventory" }))
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Turn off" }))
    await waitFor(() => expect(screen.getByRole("switch", { name: "Enable Inventory" })).not.toBeChecked())
    expect(bodies).toEqual([{ enabled: false, version: 3 }])
    await userEvent.click(screen.getByRole("switch", { name: "Enable Inventory" }))
    await waitFor(() => expect(bodies).toEqual([{ enabled: false, version: 3 }, { enabled: true, version: 4 }]))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("reloads after 409, retains the explanation and sends the refreshed version on the next edit", async () => {
    setup()
    let gets = 0
    const versions: number[] = []
    server.use(http.get("*/api/v1/ai/agents", () => HttpResponse.json({ items: [{ ...TEAM_AGENT, version: ++gets === 1 ? 3 : 4, daily_cost_limit_usd: gets === 1 ? "1.00" : "2.00" }] })), http.patch(PATCH, async ({ request }) => {
      versions.push((await request.json() as { version: number }).version)
      return HttpResponse.json({ title: "Conflict", status: 409, code: "ai.agent_version_conflict" }, { status: 409 })
    }))
    renderApp("/ai/team")
    await userEvent.click(await screen.findByRole("button", { name: "Save" }))
    expect(await screen.findByText("Someone else changed this agent; check and try again")).toBeInTheDocument()
    expect(screen.getByLabelText("Daily cost limit (USD)")).toHaveValue("2.00")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(versions).toEqual([3, 4]))
  })

  it.each(["-1", "0.001", "abc", "1e2", "9007199254740992"])("rejects invalid limit %s without PATCH", async (value) => {
    setup()
    const patch = vi.fn()
    server.use(http.patch(PATCH, patch))
    renderApp("/ai/team")
    const input = await screen.findByLabelText("Daily cost limit (USD)")
    await userEvent.clear(input)
    await userEvent.type(input, value)
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    expect(screen.getByText(/Enter a non-negative amount/)).toBeInTheDocument()
    expect(patch).not.toHaveBeenCalled()
  })

  it.each([["", null], ["0", "0.00"], ["12.34", "12.34"]])("saves limit %s as %s", async (value, expected) => {
    setup()
    let sent: unknown
    server.use(http.patch(PATCH, async ({ request }) => { sent = await request.json(); return HttpResponse.json(TEAM_AGENT) }))
    renderApp("/ai/team")
    const input = await screen.findByLabelText("Daily cost limit (USD)")
    await userEvent.clear(input)
    if (value) await userEvent.type(input, value)
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toEqual({ daily_cost_limit_usd: expected, version: 3 }))
  })

  it.each(["/ai/team", "/ai/team/inventory", "/ai/chains/chain-1"])("does not fetch protected data without read permission at %s", async (path) => {
    mockMe({ ...ME, permissions: [] })
    const read = vi.fn(() => HttpResponse.json({ items: [TEAM_AGENT] }))
    server.use(http.get("*/api/v1/ai/agents", read), http.get("*/api/v1/ai/chains/chain-1", read), http.get("*/api/v1/ai/agents/inventory/activity", read))
    renderApp(path)
    expect(await screen.findByText("You don't have access to AI Activity.")).toBeInTheDocument()
    expect(read).not.toHaveBeenCalled()
  })

  it("loads detail timeline cursor pages and displays delivery evidence and links", async () => {
    setup()
    const cursors: (string | null)[] = []
    server.use(http.get("*/api/v1/ai/agents/inventory/activity", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor")
      cursors.push(cursor)
      return HttpResponse.json(cursor ? { items: [teamItem("3", { kind: "delivery", delivery_state: "failed", title: "Failed PO" }), teamItem("4", { kind: "delivery", delivery_state: "stopped", title: "Stopped PO" })], next_cursor: null } : { items: [teamItem("1", { kind: "delivery", delivery_state: "scheduled", proposal_id: "proposal-1", result_link: "/purchasing/quote-requests/quote-1", title: "Queued PO" }), teamItem("2", { delivery_state: "delivered", title: "Delivered PO" })], next_cursor: "opaque+cursor/=" })
    }))
    renderApp("/ai/team/inventory")
    expect(await screen.findByText("Queued PO")).toBeInTheDocument()
    expect(screen.getByText("Delivered PO")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "View proposal" })).toHaveAttribute("href", "/approvals/proposal-1")
    expect(screen.getByRole("link", { name: "View result" })).toHaveAttribute("href", "/purchasing/quote-requests/quote-1")
    expect(screen.getAllByRole("link", { name: "View chain" })[0]).toHaveAttribute("href", "/ai/chains/chain-1")
    await userEvent.click(screen.getByRole("button", { name: "Load more" }))
    expect(await screen.findByText("Failed PO")).toBeInTheDocument()
    expect(screen.getByText("Stopped PO")).toBeInTheDocument()
    expect(screen.getByText("Delivery: Scheduled")).toBeInTheDocument()
    expect(screen.getByText("Delivery: Delivered")).toBeInTheDocument()
    expect(screen.getByText("Delivery: Failed")).toBeInTheDocument()
    expect(screen.getByText("Stopped")).toBeInTheDocument()
    expect(cursors).toEqual([null, "opaque+cursor/="])
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument()
  })

  it.each(["ai.chain_cost_limit", "Purchasing may not start Marketing work"])("shows chain ceilings, ordered hand-offs and stop reason %s", async (reason) => {
    setup([TEAM_AGENT, { ...TEAM_AGENT, key: "purchasing", name: "Purchasing" }], false)
    server.use(http.get("*/api/v1/ai/chains/chain-1", () => HttpResponse.json({ correlation_id: "chain-1", model_calls: 7, max_model_calls: 30, actions: 2, max_actions: 10, cost_micros: 500000, cost_limit_micros: 500000, max_depth: 6, stopped_reason: reason, steps: [teamItem("1", { title: "First step" }), teamItem("2", { title: "Next step", agent: "purchasing" })] })))
    renderApp("/ai/chains/chain-1")
    expect(await screen.findByText(reason === "ai.chain_cost_limit" ? "Stopped: this chain used its $0.50 budget" : `Stopped: ${reason}`)).toBeInTheDocument()
    for (const text of ["7 / 30", "2 / 10", "$0.50 / $0.50", "Limit: 6"]) expect(screen.getByText(text)).toBeInTheDocument()
    const list = screen.getByRole("list", { name: "Activity timeline" })
    const steps = within(list).getAllByRole("listitem")
    expect(steps[0]).toHaveTextContent("First step")
    expect(steps[1]).toHaveTextContent("Next step")
    expect(within(steps[1]!).getByLabelText("hands off to")).toBeInTheDocument()
  })

  it("filters Activity to one agent's paginated timeline and shows the agent name", async () => {
    setup([TEAM_AGENT], false)
    server.use(http.get("*/api/v1/ai/autonomy/activity", () => HttpResponse.json({ items: [], next_cursor: null })), http.get("*/api/v1/ai/agents/inventory/activity", () => HttpResponse.json({ items: [teamItem("1", { title: "Inventory-only work" })], next_cursor: null })))
    renderApp("/ai/activity")
    await userEvent.selectOptions(await screen.findByLabelText("Agent"), await screen.findByRole("option", { name: "Inventory" }))
    expect(await screen.findByText("Inventory-only work")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Inventory" })).toHaveAttribute("href", "/ai/team/inventory")
    await userEvent.selectOptions(screen.getByLabelText("Agent"), "")
    await waitFor(() => expect(screen.queryByText("Inventory-only work")).not.toBeInTheDocument())
  })

  it("filters Work queue by all of an agent's actions even across an initially empty page", async () => {
    setup([TEAM_AGENT], false)
    server.use(http.get("*/api/v1/ai/work-queue", ({ request }) => HttpResponse.json({ items: [new URL(request.url).searchParams.get("cursor") ? { id: "2", kind: "proposal", title: "Stock draft", action_type: TEAM_ACTION, proposal_id: "p2", created_at: TEAM_NOW, status: "pending_review" } : { id: "1", kind: "proposal", title: "Other draft", action_type: "marketing.send_campaign", proposal_id: "p1", created_at: TEAM_NOW, status: "pending_review" }], next_cursor: new URL(request.url).searchParams.get("cursor") ? null : "next" })))
    renderApp("/ai/work-queue?agent=inventory")
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }))
    expect(await screen.findByText("Stock draft")).toBeInTheDocument()
    expect(screen.queryByText("Other draft")).not.toBeInTheDocument()
  })

  it("keeps reserved Customer Care off while Coming soon", async () => {
    setup([{ ...TEAM_AGENT, key: "customer_care", name: "Customer Care", status: "off", enabled: false, reason: "No triggers yet; coming soon" }])
    renderApp("/ai/team")
    expect(await screen.findByRole("switch", { name: "Enable Customer Care" })).toBeDisabled()
  })

  it("permits the Customer Care switch when backend reservation has cleared", async () => {
    setup([{ ...TEAM_AGENT, key: "customer_care", name: "Customer Care", status: "off", enabled: false, reason: null }])
    renderApp("/ai/team")
    expect(await screen.findByRole("switch", { name: "Enable Customer Care" })).toBeEnabled()
  })

  it("loads the next timeline cursor when the scroll sentinel enters view", async () => {
    setup()
    let notify: IntersectionObserverCallback | undefined
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: IntersectionObserverCallback) { notify = callback }
      observe() {}
      disconnect() {}
    })
    const cursors: (string | null)[] = []
    server.use(http.get("*/api/v1/ai/agents/inventory/activity", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor"); cursors.push(cursor)
      return HttpResponse.json({ items: [teamItem(cursor ?? "first")], next_cursor: cursor ? null : "scroll-next" })
    }))
    renderApp("/ai/team/inventory")
    await screen.findByText("Stock check first")
    await waitFor(() => expect(notify).toBeDefined())
    act(() => notify!([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver))
    expect(await screen.findByText("Stock check scroll-next")).toBeInTheDocument()
    expect(cursors).toEqual([null, "scroll-next"])
  })

  it("filters Autonomy to the action linked by Team", async () => {
    setup()
    server.use(http.get("*/api/v1/ai/autonomy/rules", () => HttpResponse.json({ paused: false, items: [TEAM_AGENT.autonomy[0], { ...TEAM_AGENT.autonomy[0], action_type: "conversations.send_reply" }] })))
    renderApp(`/ai/autonomy?action=${encodeURIComponent(TEAM_ACTION)}`)
    expect(await screen.findByText("Create reorder drafts when stock is low")).toBeInTheDocument()
    expect(screen.queryByText("Front-desk replies")).not.toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Show all actions" })).toHaveAttribute("href", "/ai/autonomy")
  })

  it("shows agent metadata on all-agent Activity items when supplied", async () => {
    setup()
    server.use(http.get("*/api/v1/ai/autonomy/activity", () => HttpResponse.json({ items: [{ id: "eval-1", agent: "inventory", action_type: TEAM_ACTION, created_at: TEAM_NOW, proposal_id: "p1", title: "Stock evaluation", status: "succeeded", delivery_state: "none", failure_code: null, granted_by: null, granted_by_name: "", outcome: "acted", reasons: [], reorder_draft_id: null, result_link: null, rule_id: "r1", rule_version: 1 }], next_cursor: null })))
    renderApp("/ai/activity")
    await screen.findByRole("link", { name: "Stock evaluation" })
    await waitFor(() => expect(screen.getByRole("link", { name: "Stock evaluation" }).closest("tr")).toHaveTextContent("Inventory"))
  })

})
