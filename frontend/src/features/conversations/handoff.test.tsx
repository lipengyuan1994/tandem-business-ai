import { act, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { toast } from "sonner"
import { describe, expect, it, vi } from "vitest"
import type { Thread } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const ID = "00000000-0000-7000-8000-0000000000f1"
const base = `*/api/v1/conversations/${ID}`
const THREAD: Thread = {
  id: ID, channel: "web_chat", status: "waiting_on_staff", subject: "Warranty question", customer_id: null, customer_name: null, visitor_name: "Ada Example", visitor_email: null,
  assigned_to: null, handoff_requested_at: "2026-11-05T17:00:00Z", last_message_at: "2026-11-05T17:00:00Z", version: 2,
  ai_mode: "auto", ai_generation: 0, ai_paused_at: null, contact_consent_at: null, contact_consent_text: null,
  messages: [{ id: "reply-1", direction: "outbound", author_kind: "assistant", author_user_id: null, body: "Warranty draft", created_at: "2026-11-05T17:00:00Z", delivery_status: null, proposal_id: null,
    publication_state: "withheld", publish_at: new Date(Date.now() + 120000).toISOString(), sending_label: "Sending in 2:00", stop_url: "/api/v1/conversations/messages/reply-1/stop" }],
}
function setup(perms: string[] = []) {
  mockMe(meWith("conversations.thread.read", ...perms))
  let thread = structuredClone(THREAD)
  const actions: string[] = []
  server.use(
    http.get("*/api/v1/conversations", () => HttpResponse.json({ items: [{ ...thread, last_message_preview: "Warranty question" }], next_cursor: null })),
    http.get(base, () => HttpResponse.json(thread)),
    http.get("*/api/v1/crm/customers/match", () => HttpResponse.json([])),
    http.post(`${base}/take-over`, () => { actions.push("take-over"); thread = { ...thread, ai_mode: "human", ai_generation: 1 }; return HttpResponse.json(thread) }),
    http.post(`${base}/hand-back`, () => { actions.push("hand-back"); thread = { ...thread, ai_mode: "auto", ai_generation: 2, handoff_requested_at: null }; return HttpResponse.json(thread) }),
    http.post("*/api/v1/conversations/messages/reply-1/stop", () => { actions.push("stop"); thread = { ...thread, messages: thread.messages.map((m) => ({ ...m, publication_state: "stopped", stop_url: null })) }; return HttpResponse.json(thread.messages[0]) }),
  )
  return actions
}

describe("Inbox AI ownership and publication", () => {
  it("shows ownership and hand-over request; takes over and hands back through the scoped endpoints", async () => {
    const actions = setup(["conversations.thread.write"])
    renderApp(`/inbox/${ID}`)
    expect((await screen.findAllByText("Hand-over requested")).length).toBeGreaterThan(0)
    expect(screen.getAllByText("AI is answering").length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole("button", { name: "Take over" }))
    await screen.findByRole("button", { name: "Hand back to AI" })
    expect(screen.getAllByText("A person is handling this").length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole("button", { name: "Hand back to AI" }))
    await screen.findByRole("button", { name: "Take over" })
    expect(actions).toEqual(["take-over", "hand-back"])
    expect(screen.queryByText("Hand-over requested")).not.toBeInTheDocument()
  })

  it.each([{ perms: [] }, { perms: ["conversations.thread.write"] }])("does not expose Stop without action-approve or settings permission: %j", async ({ perms }) => {
    setup(perms)
    renderApp(`/inbox/${ID}`)
    await screen.findByText("Warranty draft")
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument()
    if (!perms.length) expect(screen.queryByRole("button", { name: "Take over" })).not.toBeInTheDocument()
  })

  it.each(["conversations.proposal.approve", "org.settings.write"])("counts down live and confirms Stop with %s", async (permission) => {
    const actions = setup([permission])
    const publishAt = new Date(Date.now() + 65000).toISOString()
    server.use(http.get(base, () => HttpResponse.json({ ...THREAD, messages: [{ ...THREAD.messages[0], publish_at: publishAt }] })))
    let tick: (() => void) | undefined
    const original = window.setInterval
    const spy = vi.spyOn(window, "setInterval").mockImplementation((fn, ms, ...args) => { if (ms === 1000 && typeof fn === "function") tick = fn as () => void; return original(fn, ms, ...args) })
    try {
      renderApp(`/inbox/${ID}`)
      await screen.findByText(/Sending in 1:0[45]/)
      const now = vi.spyOn(Date, "now").mockReturnValue(Date.parse(publishAt) - 30000)
      act(() => tick?.())
      expect(screen.getByText("Sending in 0:30")).toBeInTheDocument()
      now.mockRestore()
      await userEvent.click(screen.getByRole("button", { name: "Stop" }))
      expect(actions).toEqual([])
      await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop reply" }))
      await waitFor(() => expect(actions).toEqual(["stop"]))
      expect(await screen.findByText("Stopped")).toBeInTheDocument()
    } finally { spy.mockRestore(); vi.restoreAllMocks() }
  })

  it.each(["human_mode", "stale_reply"])("explains a cancelled %s reply to staff", async (reason) => {
    setup()
    server.use(http.get(base, () => HttpResponse.json({ ...THREAD, messages: [{ ...THREAD.messages[0], publication_state: "cancelled", cancellation_reason: reason, stop_url: null }] })))
    renderApp(`/inbox/${ID}`)
    expect(await screen.findByText(reason === "human_mode" ? "Cancelled: A person is handling this conversation." : "Cancelled: A newer message or ownership change made this reply out of date.")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument()
  })

  it("reports a publication race and does not claim Stop succeeded", async () => {
    setup(["conversations.proposal.approve"])
    server.use(http.post("*/api/v1/conversations/messages/reply-1/stop", () => HttpResponse.json({ code: "conversations.too_late_to_stop", status: 409 }, { status: 409 })))
    renderApp(`/inbox/${ID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }))
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop reply" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Too late to stop")
    expect(screen.queryByText("Stopped")).not.toBeInTheDocument()
  })
})

it("keeps a too-late notice when refreshed publication removes the Stop control", async () => {
  toast.dismiss()
  setup(["conversations.proposal.approve"])
  let published = false
  server.use(
    http.get(base, () => HttpResponse.json({ ...THREAD, messages: THREAD.messages.map((m) => ({ ...m, publication_state: published ? "published" : "withheld", stop_url: published ? null : m.stop_url })) })),
    http.post("*/api/v1/conversations/messages/reply-1/stop", () => { published = true; return HttpResponse.json({ code: "conversations.too_late_to_stop", status: 409 }, { status: 409 }) }),
  )
  renderApp(`/inbox/${ID}`)
  await waitFor(() => expect(screen.queryByText(/Too late to stop: this reply/)).not.toBeInTheDocument())
  await userEvent.click(await screen.findByRole("button", { name: "Stop" }))
  await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop reply" }))
  await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument())
  expect(screen.getByText(/Too late to stop: this reply/)).toBeInTheDocument()
})
