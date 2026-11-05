import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { ME, meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import type { ActivityEntry, AutonomyRule } from "./autonomy-api"

const BASE = "*/api/v1/ai/autonomy"
const ACTION = "inventory.create_reorder_draft"
const RULE: AutonomyRule = { id: "rule-1", action_type: ACTION, level: "act_notify", shadow: true, daily_cap: 10, active: true, version: 3, granted_by: ME.user.id, created_at: null, updated_at: null, summary: { would_act: 5, acted: 2, fell_back: 1 } }
const PERMS = ["org.settings.write", "inventory.proposal.approve", "inventory.reorder.read"]

function mockRules(rule = RULE, paused = false) {
  mockMe(meWith(...PERMS))
  server.use(http.get(`${BASE}/rules`, () => HttpResponse.json({ paused, items: [rule] })))
}

describe("autonomy settings", () => {
  it("shows levels, unavailable Draft, switches and 30-day counts; saves with the loaded version", async () => {
    mockRules()
    let sent: unknown
    server.use(http.put(`${BASE}/rules`, async ({ request }) => {
      sent = await request.json()
      return HttpResponse.json({ ...RULE, level: "act", shadow: false, daily_cap: 4, active: false, version: 4 })
    }))
    renderApp("/ai/autonomy")
    expect(await screen.findByText("Create reorder drafts when stock is low")).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "Draft" })).toBeDisabled()
    expect(screen.getByRole("radio", { name: "Act and notify" })).toBeChecked()
    expect(screen.getByRole("radio", { name: "Off" })).toBeEnabled()
    expect(screen.getByRole("radio", { name: "Ask" })).toBeEnabled()
    expect(screen.getByText("Would act: 5 · Acted: 2 · Fell back: 1")).toBeInTheDocument()
    expect(screen.getByRole("switch", { name: "Shadow mode" })).toBeChecked()
    await userEvent.click(screen.getByRole("radio", { name: "Act" }))
    await userEvent.click(screen.getByRole("switch", { name: "Shadow mode" }))
    await userEvent.click(screen.getByRole("switch", { name: "Rule active" }))
    await userEvent.clear(screen.getByLabelText("Daily limit"))
    await userEvent.type(screen.getByLabelText("Daily limit"), "4")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toEqual({ action_type: ACTION, level: "act", shadow: false, daily_cap: 4, active: false, version: 3 }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled())
  })

  it("shows the server's forced shadow state after creating a new rule", async () => {
    mockRules({ ...RULE, id: null, version: 0, level: "ask" })
    server.use(http.put(`${BASE}/rules`, () => HttpResponse.json({ ...RULE, version: 1, shadow: true, level: "act_notify" })))
    renderApp("/ai/autonomy")
    await userEvent.click(await screen.findByRole("switch", { name: "Shadow mode" }))
    await userEvent.click(screen.getByRole("radio", { name: "Act and notify" }))
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(screen.getByRole("switch", { name: "Shadow mode" })).toBeChecked())
    expect(screen.queryByText(/New rules always start/)).not.toBeInTheDocument()
  })

  it("offers a reload on 409 and loads the latest version without overwriting it", async () => {
    mockRules()
    let gets = 0
    let puts = 0
    server.use(
      http.get(`${BASE}/rules`, () => HttpResponse.json({ paused: false, items: [{ ...RULE, version: ++gets === 1 ? 3 : 4, level: gets === 1 ? "act_notify" : "off" }] })),
      http.put(`${BASE}/rules`, () => { puts++; return HttpResponse.json({ code: "approvals.autonomy_version_conflict", status: 409, title: "Conflict" }, { status: 409 }) }),
    )
    renderApp("/ai/autonomy")
    await userEvent.click(await screen.findByRole("button", { name: "Save" }))
    const dialog = await screen.findByRole("dialog")
    await userEvent.click(within(dialog).getByRole("button", { name: /Reload/ }))
    await waitFor(() => expect(screen.getByRole("radio", { name: "Off" })).toBeChecked())
    expect(puts).toBe(1)
  })

  it("requires confirmation to pause, shows the business banner and resumes", async () => {
    mockRules()
    let pauses = 0
    let resumes = 0
    server.use(
      http.post(`${BASE}/pause`, () => { pauses++; return HttpResponse.json({ paused: true }) }),
      http.post(`${BASE}/resume`, () => { resumes++; return HttpResponse.json({ paused: false }) }),
    )
    renderApp("/ai/autonomy")
    await userEvent.click(await screen.findByRole("button", { name: "Pause all AI actions" }))
    expect(pauses).toBe(0)
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }))
    expect(pauses).toBe(0)
    await userEvent.click(screen.getByRole("button", { name: "Pause all AI actions" }))
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Pause actions" }))
    expect(await screen.findByText(/All AI actions are paused for this business/)).toHaveAttribute("role", "status")
    expect(pauses).toBe(1)
    await userEvent.click(screen.getByRole("button", { name: "Resume AI actions" }))
    await waitFor(() => expect(screen.queryByText(/All AI actions are paused for this business/)).not.toBeInTheDocument())
    expect(resumes).toBe(1)
  })

  it("shows a paused banner on load and rejects invalid daily limits", async () => {
    mockRules(RULE, true)
    renderApp("/ai/autonomy")
    expect(await screen.findByText(/All AI actions are paused for this business/)).toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText("Daily limit"))
    await userEvent.type(screen.getByLabelText("Daily limit"), "-1")
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    expect(screen.getByText(/Enter a whole number from 0/)).toBeInTheDocument()
  })

  it("hides settings and navigation from operators and makes no rules request", async () => {
    mockMe()
    let gets = 0
    server.use(http.get(`${BASE}/rules`, () => { gets++; return HttpResponse.json({ paused: false, items: [RULE] }) }))
    renderApp("/ai/autonomy")
    expect(await screen.findByText("You don't have access to autonomy settings.")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Autonomy" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
    expect(gets).toBe(0)
    expect(screen.getByRole("link", { name: "Activity" })).toBeInTheDocument()
  })
})

const entry = (id: string, overrides: Partial<ActivityEntry> = {}): ActivityEntry => ({ id, proposal_id: `proposal-${id}`, rule_id: "rule-1", rule_version: 3, outcome: "acted", reasons: [], created_at: "2026-11-05T17:00:00Z", title: `Reorder ${id}`, action_type: ACTION, granted_by: ME.user.id, granted_by_name: "Morgan Example", status: "succeeded", failure_code: null, reorder_draft_id: "draft-1", result_link: "/api/v1/inventory/reorder-drafts/draft-1", ...overrides })

describe("AI Activity", () => {
  it("shows outcomes, plain reasons, business-local time, current result links and pagination", async () => {
    mockMe(meWith(...PERMS))
    server.use(http.get(`${BASE}/activity`, ({ request }) => {
      if (new URL(request.url).searchParams.get("cursor") === "older+/=") return HttpResponse.json({ items: [entry("older", { outcome: "fell_back", reasons: ["rule_inactive", "grantor_not_authorized", "level_not_supported"] })], next_cursor: null })
      return HttpResponse.json({ items: [entry("acted"), entry("shadow", { outcome: "would_act", status: "pending_review", result_link: null }), entry("fallback", { outcome: "fell_back", reasons: ["cap_reached"], status: "pending_review", result_link: null }), entry("blocked", { outcome: "blocked", reasons: ["autonomy_paused"], status: "failed", failure_code: "approvals.autonomy_revoked", result_link: null })], next_cursor: "older+/=" })
    }))
    renderApp("/ai/activity")
    const table = await screen.findByRole("table")
    for (const outcome of ["Acted", "Would act (shadow)", "Fell back", "Blocked"]) expect(within(table).getByText(outcome)).toBeInTheDocument()
    expect(within(table).getByText("The daily limit was reached.")).toBeInTheDocument()
    expect(within(table).getByText("AI actions were paused.")).toBeInTheDocument()
    expect(within(table).getAllByText("5 Nov 2026 10:00 MST")).toHaveLength(4)
    expect(within(table).getByRole("link", { name: "Reorder draft created" })).toHaveAttribute("href", "/api/v1/inventory/reorder-drafts/draft-1")
    expect(within(table).getByRole("link", { name: "Reorder shadow" })).toHaveAttribute("href", "/approvals/proposal-shadow")
    expect(within(table).getAllByText("Granted by Morgan Example")).toHaveLength(4)
    expect(within(table).getAllByText("Rule rule-1 (v3)")).toHaveLength(4)
    await userEvent.click(screen.getByRole("button", { name: "Load more" }))
    expect(await screen.findByText("The rule was inactive.")).toBeInTheDocument()
    expect(screen.getByText(/no longer allowed to approve/)).toBeInTheDocument()
    expect(screen.getByText("This action does not support the selected level.")).toBeInTheDocument()
  })

  it("keeps automatic approval distinct from failed execution and hides unavailable result links", async () => {
    mockMe()
    server.use(http.get(`${BASE}/activity`, () => HttpResponse.json({ items: [entry("failed", { status: "failed", failure_code: "inventory.reorder_changed" }), entry("done", { outcome: "would_act" })], next_cursor: null })))
    renderApp("/ai/activity")
    const table = await screen.findByRole("table")
    expect(within(table).getByText("Acted")).toBeInTheDocument()
    expect(within(table).getByText("Failed")).toBeInTheDocument()
    expect(within(table).getByText("Stock or the suggested quantity changed before execution.")).toBeInTheDocument()
    expect(within(table).queryByRole("link", { name: "Reorder draft created" })).not.toBeInTheDocument()
  })

  it("explains the empty state", async () => {
    mockMe()
    server.use(http.get(`${BASE}/activity`, () => HttpResponse.json({ items: [], next_cursor: null })))
    renderApp("/ai/activity")
    expect(await screen.findByText("No AI activity yet.")).toBeInTheDocument()
    expect(screen.getByText(/When an autonomy rule evaluates an AI proposal/)).toBeInTheDocument()
  })

  it("does not show Activity or fetch data without proposal read permission", async () => {
    mockMe({ ...ME, permissions: [] })
    renderApp("/ai/activity")
    expect(await screen.findByText("You don't have access to AI Activity.")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Activity" })).not.toBeInTheDocument()
  })
})
