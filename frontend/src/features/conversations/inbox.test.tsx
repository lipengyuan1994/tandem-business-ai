import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { Thread } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const TID = "00000000-0000-7000-8000-0000000000f1"
const THREAD: Thread = {
  id: TID, channel: "web_chat", status: "waiting_on_staff", subject: null, customer_id: null, customer_name: null, visitor_name: "Ada", visitor_email: "ada@example.com",
  ai_mode: "auto", ai_generation: 0, ai_paused_at: null,
  assigned_to: null, emergency: false, handoff_requested_at: null, last_message_at: "2026-11-05T17:00:00Z", version: 2,
  contact_consent_at: null, contact_consent_text: null,
  messages: [{ id: "m1", direction: "inbound", author_kind: "visitor", author_user_id: null, body: "My rear brake squeaks. Next Tuesday morning?", created_at: "2026-11-05T17:00:00Z", delivery_status: null, proposal_id: null, publication_state: "published", sending_label: null, stop_url: null }],
}

function mockInbox() {
  const sent: unknown[] = []
  const patched: unknown[] = []
  server.use(
    http.get("*/api/v1/conversations", () => HttpResponse.json({ next_cursor: null, items: [{ ...THREAD, last_message_preview: "My rear brake squeaks." }] })),
    http.get(`*/api/v1/conversations/${TID}`, () => HttpResponse.json(THREAD)),
    http.post(`*/api/v1/conversations/${TID}/messages`, async ({ request }) => {
      const body = await request.json()
      sent.push(body)
      return HttpResponse.json({ id: "m2", author_kind: "staff", author_user_id: null, created_at: "2026-11-05T17:05:00Z", delivery_status: null, proposal_id: null, ...(body as object) }, { status: 201 })
    }),
    http.patch(`*/api/v1/conversations/${TID}`, async ({ request }) => { patched.push(await request.json()); return HttpResponse.json({ ...THREAD, customer_id: "c1", customer_name: "Ada Example" }) }),
    http.get("*/api/v1/crm/customers/match", () => HttpResponse.json([{ customer_id: "c1", display_name: "Ada Example", reason: "name", score: 0.9 }])),
  )
  return { sent, patched }
}

describe("inbox", () => {
  it.each([null, "2026-11-05T17:00:00Z"])("shows visitor consent only when present: %s", async (at) => {
    mockMe(meWith("conversations.thread.read"))
    mockInbox()
    server.use(http.get(`*/api/v1/conversations/${TID}`, () => HttpResponse.json({ ...THREAD, contact_consent_at: at, contact_consent_text: at ? "v1: You may contact me about this request by e-mail." : null })))
    renderApp(`/inbox/${TID}`)
    await screen.findByText("My rear brake squeaks. Next Tuesday morning?")
    const line = screen.queryByText(/Visitor agreed to be contacted about this request/)
    if (at) {
      expect(line).toBeInTheDocument()
      expect(line?.querySelector("time")).toHaveAttribute("dateTime", at)
      expect(line).toHaveTextContent("5 Nov 2026")
    } else expect(line).not.toBeInTheDocument()
  })

  it("shows list, thread and customer panes; the internal-note toggle sends an internal message", async () => {
    mockMe(meWith("conversations.thread.read", "conversations.thread.write", "crm.customer.read"))
    const { sent } = mockInbox()
    renderApp(`/inbox/${TID}`)
    expect(await screen.findByRole("navigation", { name: "Conversations" })).toBeInTheDocument()
    expect(await screen.findByText("My rear brake squeaks. Next Tuesday morning?")).toBeInTheDocument()
    expect(screen.getByRole("complementary", { name: "Customer" })).toHaveTextContent("ada@example.com")

    await userEvent.click(screen.getByRole("checkbox", { name: "Internal note" }))
    await userEvent.type(screen.getByPlaceholderText("Only staff will see this…"), "Probably needs new pads")
    await userEvent.click(screen.getByRole("button", { name: "Add note" }))
    await waitFor(() => expect(sent).toEqual([{ direction: "internal", body: "Probably needs new pads" }]))

    await userEvent.click(screen.getByRole("checkbox", { name: "Internal note" }))
    await userEvent.type(screen.getByPlaceholderText("Write a reply…"), "Tuesday 11:45 works!")
    await userEvent.click(screen.getByRole("button", { name: "Send reply" }))
    await waitFor(() => expect(sent).toHaveLength(2))
    expect(sent[1]).toEqual({ direction: "outbound", body: "Tuesday 11:45 works!" })
  })

  it("links the conversation to a matching customer with the current version", async () => {
    mockMe(meWith("conversations.thread.read", "conversations.thread.write", "crm.customer.read"))
    const { patched } = mockInbox()
    renderApp(`/inbox/${TID}`)
    const card = await screen.findByRole("complementary", { name: "Customer" })
    await userEvent.click(await within(card).findByRole("button", { name: "Link to Ada Example" }))
    await waitFor(() => expect(patched).toEqual([{ version: 2, customer_id: "c1" }]))
  })

  it("is read-only without write permission", async () => {
    mockMe(meWith("conversations.thread.read"))
    mockInbox()
    renderApp(`/inbox/${TID}`)
    expect(await screen.findByText("My rear brake squeaks. Next Tuesday morning?")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Send reply" })).not.toBeInTheDocument()
  })

  it("does not carry a half-written reply from one conversation into another", async () => {
    mockMe(meWith("conversations.thread.read", "conversations.thread.write", "crm.customer.read"))
    const other = { ...THREAD, id: "00000000-0000-7000-8000-0000000000f2", visitor_name: "Ben", visitor_email: "ben@example.com" }
    server.use(
      http.get("*/api/v1/conversations", () => HttpResponse.json({ next_cursor: null, items: [{ ...THREAD, last_message_preview: "A" }, { ...other, last_message_preview: "B" }] })),
      http.get(`*/api/v1/conversations/${TID}`, () => HttpResponse.json(THREAD)),
      http.get(`*/api/v1/conversations/${other.id}`, () => HttpResponse.json(other)),
      http.get("*/api/v1/crm/customers/match", () => HttpResponse.json([])),
    )
    renderApp(`/inbox/${TID}`)
    // Visit both once so each is cached; switching between cached conversations does not unmount the thread view.
    await userEvent.click(await screen.findByRole("link", { name: /Ben/ }))
    await screen.findByText("ben@example.com")
    await userEvent.click(screen.getByRole("link", { name: /Ada/ }))
    await userEvent.type(await screen.findByPlaceholderText("Write a reply…"), "Private note for Ada")
    await userEvent.click(screen.getByRole("link", { name: /Ben/ }))
    expect(await screen.findByRole("heading", { name: "Ben" })).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Write a reply…")).toHaveValue("")
  })
})
