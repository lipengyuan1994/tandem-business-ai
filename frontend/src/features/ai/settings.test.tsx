import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { mockStatus, ON } from "./testing"
import type { RunOut, RunPage, UsageOut } from "./types"

const READ = ["ai.settings.read", "org.settings.read", "ai.assistant.use"]
const WRITE = [...READ, "ai.settings.write", "org.settings.write"]
const SETTINGS = { settings: { terminology: {}, ai_policy: "hosted_vultr", ai_daily_token_budget: null }, version: 4 }

const run = (n: number, over: Partial<RunOut> = {}): RunOut => ({
  id: `run-${n}`, use_case: "copilot", status: "succeeded", needs_human: false, model: "example-model", provider: "example", steps: 3, input_tokens: 1200, output_tokens: 300,
  cost_micros: 12_500, latency_ms: 2400, error_code: null, started_at: "2026-11-05T17:00:00Z", finished_at: "2026-11-05T17:00:03Z", ...over,
})
const USAGE: UsageOut = {
  today: { input_tokens: 1500, output_tokens: 500, cost_micros: 20_000, budget_tokens: 2_000_000, percent: 1 },
  days: [
    { day: "2026-11-04", input_tokens: 1000, output_tokens: 200, cost_micros: 5000, runs: 2 },
    { day: "2026-11-05", input_tokens: 4000, output_tokens: 1000, cost_micros: 9000, runs: 5 },
  ],
}

function mockAi(over: { runs?: RunOut[]; next?: string | null } = {}) {
  server.use(
    http.get("*/api/v1/org/settings", () => HttpResponse.json(SETTINGS)),
    http.get("*/api/v1/ai/usage", () => HttpResponse.json(USAGE)),
    http.get("*/api/v1/ai/runs", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor")
      if (cursor === "page2+/=") return HttpResponse.json({ items: [run(9, { use_case: "briefing" })], next_cursor: null } satisfies RunPage)
      return HttpResponse.json({ items: over.runs ?? [run(1)], next_cursor: over.next ?? null } satisfies RunPage)
    }),
  )
}

describe("AI settings page", () => {
  it("lists voice as off with the STT backend reason", async () => {
    mockMe(meWith(...READ))
    mockStatus({ ...ON, model_classes: { stt: { enabled: false, reason: "local-only policy without local speech-to-text" } } })
    mockAi()
    renderApp("/settings/ai")
    expect(await screen.findByText("Voice dictation: off — local-only policy without local speech-to-text")).toBeVisible()
  })

  it("does not interpret absent STT telemetry as off", async () => {
    mockMe(meWith(...READ))
    mockStatus(ON)
    mockAi()
    renderApp("/settings/ai")
    expect(await screen.findByText("Voice dictation: status unknown")).toBeVisible()
  })

  it("explains each policy, shows usage bars and the run history without any text", async () => {
    mockMe(meWith(...READ))
    mockStatus({ ...ON, budget_percent: 1 })
    mockAi({ runs: [run(1), run(2, { status: "failed", use_case: "intake", needs_human: true, cost_micros: 0, latency_ms: 900 }), run(3, { status: "budget_exceeded", use_case: "summary" })] })
    renderApp("/settings/ai")
    expect(await screen.findByText("Text is sent to Vultr's AI service for processing, and nowhere else.")).toBeInTheDocument()
    expect(screen.getByText("No text leaves this installation. AI is off if no local model is set up.")).toBeInTheDocument()
    expect(screen.getByText("In effect now: Vultr only")).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "Vultr only" })).toBeChecked()

    expect(await screen.findByText("Used today: 2,000 of 2,000,000 (1%)")).toBeInTheDocument()
    const bars = await screen.findByRole("heading", { name: "Use over the last 30 days" })
    const list = bars.parentElement!.querySelector("ol")!
    expect(within(list).getAllByRole("listitem")).toHaveLength(2)
    expect(within(list).getByText("2026-11-05: 5,000 tokens, 5 runs")).toBeInTheDocument()

    const table = await screen.findByRole("table")
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["When", "What for", "Result", "Cost", "Time"])
    const rows = within(table).getAllByRole("row").slice(1)
    expect(rows).toHaveLength(3)
    expect(within(rows[0]!).getByText("Assistant chat")).toBeInTheDocument()
    expect(within(rows[0]!).getByText("Done")).toBeInTheDocument()
    expect(within(rows[0]!).getByText("$0.0125")).toBeInTheDocument()
    expect(within(rows[0]!).getByText("2.4 s")).toBeInTheDocument()
    expect(within(rows[1]!).getByText("Failed")).toBeInTheDocument()
    expect(within(rows[1]!).getByText("A person needs to look")).toBeInTheDocument()
    expect(within(rows[2]!).getByText("Stopped: daily limit")).toBeInTheDocument()
  })

  it("shows the regulated policy as locked and cannot be changed", async () => {
    mockMe(meWith(...WRITE))
    mockStatus({ ...ON, policy: "regulated" })
    mockAi()
    renderApp("/settings/ai")
    expect(await screen.findByText(/Locked: this installation is set up for regulated data/)).toBeInTheDocument()
    expect(screen.getByText("In effect now: Regulated (locked)")).toBeInTheDocument()
    const regulated = screen.getByRole("radio", { name: "Regulated (locked)" })
    expect(regulated).toBeChecked()
    for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })

  it("saves the policy and the daily limit with the version it was loaded at", async () => {
    mockMe(meWith(...WRITE))
    mockStatus()
    mockAi()
    let sent: unknown = null
    server.use(http.patch("*/api/v1/org/settings", async ({ request }) => {
      sent = await request.json()
      return HttpResponse.json({ ...SETTINGS, version: 5 })
    }))
    renderApp("/settings/ai")
    await userEvent.click(await screen.findByRole("radio", { name: "Inside this installation only" }))
    const limit = screen.getByLabelText("Daily limit (tokens)")
    await userEvent.type(limit, "abc")
    expect(await screen.findByText("Enter a whole number, or leave it empty.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    await userEvent.clear(limit)
    await userEvent.type(limit, "500000")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toEqual({ version: 4, settings: { terminology: {}, ai_policy: "strict_private", ai_daily_token_budget: 500000 } }))
  })

  it("is read-only without the write permission, and hidden without the read permission", async () => {
    mockMe(meWith(...READ))
    mockStatus()
    mockAi()
    const first = renderApp("/settings/ai")
    await screen.findByText("You can look at these settings but not change them.")
    expect(screen.getByLabelText("Daily limit (tokens)")).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
    expect(screen.getByRole("link", { name: "AI" })).toBeInTheDocument()
    first.unmount()

    mockMe(meWith("org.settings.read"))
    renderApp("/settings/ai")
    expect(await screen.findByText("You don't have access to the AI settings.")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "AI" })).not.toBeInTheDocument()
  })

  it("loads older runs on request", async () => {
    mockMe(meWith(...READ))
    mockStatus()
    mockAi({ next: "page2+/=" })
    renderApp("/settings/ai")
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }))
    expect(await screen.findByText("Daily briefing")).toBeInTheDocument()
  })

  it("says why AI is off at the top of the page", async () => {
    mockMe(meWith(...READ))
    mockStatus({ ...ON, enabled: false, reason: "ai.not_configured" })
    mockAi({ runs: [] })
    renderApp("/settings/ai")
    expect(await screen.findByText(/AI is not set up on this installation yet/)).toBeInTheDocument()
    expect(await screen.findByText("No AI runs yet.")).toBeInTheDocument()
  })
})
