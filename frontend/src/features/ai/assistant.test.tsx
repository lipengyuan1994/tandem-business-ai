import { cleanup, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { delay, http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { mockStatus, ON, PROPOSAL, RUN, sseResponse, THREAD } from "./testing"
import type { ThreadDetailOut, ThreadMessageOut, ThreadOut } from "./types"

const USE = "ai.assistant.use"
const NOW = "2026-11-05T17:00:00Z"
const thread: ThreadOut = { id: THREAD, title: "What is booked tomorrow?", created_at: NOW, updated_at: NOW }

function detail(messages: ThreadMessageOut[]): ThreadDetailOut {
  return { ...thread, messages }
}
function msg(role: "user" | "assistant", content: string, extra: Partial<ThreadMessageOut> = {}): ThreadMessageOut {
  return { id: `m-${role}-${content.length}`, role, content, run_id: null, proposal_ids: [], created_at: NOW, ...extra }
}

async function openPanel() {
  await userEvent.click(await screen.findByRole("button", { name: "Assistant" }))
  return screen.findByRole("dialog", { name: "Assistant" })
}

describe("assistant panel", () => {
  afterEach(() => vi.restoreAllMocks())

  it("has no top-bar button for people without the permission", async () => {
    mockMe()
    renderApp("/approvals")
    await screen.findByRole("heading", { name: "Approvals", level: 1 }).catch(() => undefined)
    expect(screen.queryByRole("button", { name: "Assistant" })).not.toBeInTheDocument()
  })

  it("uses the business's own words for customers and appointments", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    server.use(
      http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings: { terminology: { customers: "clients", appointments: "bookings" } }, version: 1 })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([])),
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    expect(within(panel).getByText(/Ask about your bookings, stock, messages and clients/)).toBeInTheDocument()
  })

  it("opens from the top bar and with Ctrl+J, and always shows the fixed line", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    server.use(http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread])), http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })))
    renderApp("/approvals")
    const panel = await openPanel()
    expect(within(panel).getByText("AI can make mistakes; you approve every change")).toBeInTheDocument()
    expect(await within(panel).findByRole("button", { name: "What is booked tomorrow?" })).toBeInTheDocument()
    await userEvent.keyboard("{Escape}")
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Assistant" })).not.toBeInTheDocument())
    await userEvent.keyboard("{Control>}j{/Control}")
    expect(await screen.findByRole("dialog", { name: "Assistant" })).toBeInTheDocument()
  })

  it("starts a chat on the first question and streams steps, a proposal chip and the answer", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    let created: unknown = null
    let sent: unknown = null
    let csrf: string | null = null
    let saved = false
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json(saved ? [thread] : [])),
      http.post("*/api/v1/ai/copilot/threads", async ({ request }) => { created = await request.json(); return HttpResponse.json(thread, { status: 201 }) }),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, async () => { if (saved) await delay(300); return HttpResponse.json(detail(saved ? [
        msg("user", "Book Ada for brakes"), msg("assistant", "I found Tuesday 10:00 and drafted a booking.", { run_id: RUN, proposal_ids: [PROPOSAL] }),
      ] : [])) }),
      http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, async ({ request }) => {
        sent = await request.json()
        csrf = request.headers.get("X-CSRF-Token")
        saved = true
        return sseResponse([
          ["step", { run_id: RUN, tool: "find_slots", status_text: "Checking free times…" }],
          ["step", { run_id: RUN, tool: "propose_booking", status_text: "Drafting the booking…" }],
          ["proposal", { run_id: RUN, proposal_id: PROPOSAL, proposal_title: "Book brake service for Ada" }],
          ["final", { run_id: RUN, text: "I found Tuesday 10:00 and drafted a booking." }],
        ])
      }),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    await userEvent.type(within(panel).getByLabelText("Ask the assistant"), "Book Ada for brakes")
    await userEvent.click(within(panel).getByRole("button", { name: "Ask" }))

    await waitFor(() => expect(created).toEqual({ title: "Book Ada for brakes" }))
    await waitFor(() => expect(sent).toEqual({ content: "Book Ada for brakes" }))
    expect(csrf).toBe("csrf-test-token")
    expect(await within(panel).findByText("Checking free times…")).toBeInTheDocument()
    expect(within(panel).getByText("Drafting the booking…")).toBeInTheDocument()
    // After the answer the saved chat is shown: the question, the answer and a link to the proposal in Approvals.
    expect(await within(panel).findByText("I found Tuesday 10:00 and drafted a booking.")).toBeInTheDocument()
    await waitFor(() => expect(within(panel).queryByText("Checking free times…")).not.toBeInTheDocument())
    expect(within(panel).getByText("Book Ada for brakes", { selector: "p" })).toBeInTheDocument()
    const chip = within(panel).getByRole("link", { name: /Review proposal 1/ })
    expect(chip).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
  })

  it("shows the proposal chip with its title while the answer is still arriving", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread])),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json(detail([]))),
      http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, () => sseResponse([
        ["proposal", { run_id: RUN, proposal_id: PROPOSAL, proposal_title: "Book brake service for Ada" }],
        ["final", { run_id: RUN, text: "Drafted." }],
      ], { keepOpen: true })),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    await userEvent.click(await within(panel).findByRole("button", { name: "What is booked tomorrow?" }))
    await userEvent.type(within(panel).getByLabelText("Ask the assistant"), "Book it{Enter}")
    const chip = await within(panel).findByRole("link", { name: "Book brake service for Ada" })
    expect(chip).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
    expect(await within(panel).findByText("Drafted.")).toBeInTheDocument()
  })

  it("shows an error event in plain words and keeps the question box usable", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread])),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json(detail([]))),
      http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, () => sseResponse([
        ["step", { run_id: RUN, status_text: "Looking up the schedule…" }],
        ["error", { run_id: RUN, error_code: "ai.provider_unavailable" }],
      ])),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    await userEvent.click(await within(panel).findByRole("button", { name: "What is booked tomorrow?" }))
    await userEvent.type(within(panel).getByLabelText("Ask the assistant"), "Anything free?")
    await userEvent.click(within(panel).getByRole("button", { name: "Ask" }))
    const alert = await within(panel).findByRole("alert")
    expect(alert).toHaveTextContent("The AI service did not answer. Nothing was changed.")
    expect(within(panel).getByLabelText("Ask the assistant")).toBeEnabled()
  })

  it("explains a refused request (daily limit) and a stream that stops early", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    let calls = 0
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread])),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json(detail([]))),
      http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, () => {
        calls += 1
        if (calls === 1) return HttpResponse.json({ code: "ai.budget_exceeded", status: 429, title: "Budget" }, { status: 429, headers: { "Content-Type": "application/problem+json" } })
        return sseResponse([["step", { run_id: RUN, status_text: "Working…" }]]) // ends without a final or error event
      }),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    await userEvent.click(await within(panel).findByRole("button", { name: "What is booked tomorrow?" }))
    const box = within(panel).getByLabelText("Ask the assistant")
    await userEvent.type(box, "First")
    await userEvent.click(within(panel).getByRole("button", { name: "Ask" }))
    expect(await within(panel).findByText(/Today's AI limit has been used up/)).toBeInTheDocument()
    await userEvent.type(box, "Second")
    await userEvent.click(within(panel).getByRole("button", { name: "Ask" }))
    expect(await within(panel).findByText(/The answer was cut off/)).toBeInTheDocument()
  })

  it("explains the platform rate limit before streaming starts", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread])),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json(detail([]))),
      http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, () => HttpResponse.json(
        { code: "platform.rate_limited", status: 429, title: "Too many requests" },
        { status: 429, headers: { "Content-Type": "application/problem+json", "Retry-After": "60" } },
      )),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    await userEvent.click(await within(panel).findByRole("button", { name: "What is booked tomorrow?" }))
    await userEvent.type(within(panel).getByLabelText("Ask the assistant"), "Hello{Enter}")
    expect(await within(panel).findByRole("alert")).toHaveTextContent("You have asked a lot of questions this hour. Please wait a little and try again.")
    expect(within(panel).getByLabelText("Ask the assistant")).toBeEnabled()
  })

  it("stops reading the answer when the page goes away", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    const signals: AbortSignal[] = []
    const realFetch = globalThis.fetch
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (String(input instanceof Request ? input.url : input).endsWith("/messages") && init?.signal) signals.push(init.signal)
      return realFetch(input, init)
    })
    let started = false
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread])),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json(detail([]))),
      http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, () => {
        started = true
        return sseResponse([["step", { run_id: RUN, status_text: "Thinking…" }]], { keepOpen: true })
      }),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    await userEvent.click(await within(panel).findByRole("button", { name: "What is booked tomorrow?" }))
    await userEvent.type(within(panel).getByLabelText("Ask the assistant"), "Hello")
    await userEvent.click(within(panel).getByRole("button", { name: "Ask" }))
    expect(await within(panel).findByText("Thinking…")).toBeInTheDocument()
    expect(started).toBe(true)
    expect(signals).toHaveLength(1)
    expect(signals[0]!.aborted).toBe(false)
    cleanup() // unmounts the app, as leaving the page does
    expect(signals[0]!.aborted).toBe(true)
    spy.mockRestore()
  })

  it("loads an earlier chat with the proposals its answers made", async () => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus()
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread])),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json(detail([msg("user", "Reorder pads"), msg("assistant", "Drafted a reorder.", { proposal_ids: [PROPOSAL] })]))),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    await userEvent.click(await within(panel).findByRole("button", { name: "What is booked tomorrow?" }))
    expect(await within(panel).findByText("Drafted a reorder.")).toBeInTheDocument()
    expect(within(panel).getByRole("link", { name: "Review proposal 1" })).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
    await userEvent.click(within(panel).getByRole("button", { name: "New chat" }))
    expect(await within(panel).findByText("Ask a question to get started.")).toBeInTheDocument()
  })

  it.each([
    ["ai.not_configured", /AI is not set up on this installation yet/],
    ["ai.disabled_by_policy", /AI is turned off by your privacy settings/],
    ["ai.budget_exceeded", /Today's AI limit has been used up/],
  ])("says why AI is off (%s) and stops questions", async (reason, text) => {
    mockMe(meWith(USE, "approvals.proposal.read"))
    mockStatus({ ...ON, enabled: false, reason, budget_percent: 100 })
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([])),
    )
    renderApp("/approvals")
    const panel = await openPanel()
    expect(await within(panel).findByText(text)).toBeInTheDocument()
    expect(within(panel).getByLabelText("Ask the assistant")).toBeDisabled()
    expect(within(panel).getByRole("button", { name: "Ask" })).toBeDisabled()
  })
})
