import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { toast } from "sonner"
import type { Schemas } from "@/api/types"
import { ME, meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { mockStatus, PROPOSAL, THREAD } from "./testing"

const NOW = "2026-11-05T17:00:00Z"
const BASE = `*/api/v1/ai/copilot/threads/${THREAD}`
const CARD: Schemas["ChatCard"] = { id: PROPOSAL, title: "Order brake cables", action_type: "purchasing.create_po", preview: [{ label: "Quantity", before: "2", after: "10" }], status: "pending_review", can_confirm: true, cannot_confirm_reason: null, revision_id: "revision-1", payload_hash: "a".repeat(64), delivery_state: "none", result: {} }
const thread = (card: Schemas["ChatCard"]) => ({ id: THREAD, title: "Order cables", created_at: NOW, updated_at: NOW, messages: [{ id: "message-1", role: "assistant", content: "Please review this action.", proposal_ids: [PROPOSAL], proposals: [card], run_id: "run-1", created_at: NOW }] })
function setup(card = CARD) {
  mockMe(meWith("ai.assistant.use", "purchasing.proposal.approve", "purchasing.po.read"))
  mockStatus()
  server.use(
    http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread(card)])),
    http.get(BASE, () => HttpResponse.json(thread(card))),
    http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([])),
  )
}
async function openCard() {
  renderApp("/approvals")
  await userEvent.click(await screen.findByRole("button", { name: "Assistant" }))
  await userEvent.click(await screen.findByRole("button", { name: "Order cables" }))
  return screen.findByRole("article", { name: CARD.title })
}
const conflict = (code: string) => HttpResponse.json({ code, title: "Conflict", status: 409 }, { status: 409 })

describe("assistant action cards", () => {
  it("renders preview, revision-bound confirm, executing and result and refreshes the thread", async () => {
    setup()
    let card = CARD
    let gets = 0
    let sent: unknown
    let csrf: string | null = null
    server.use(
      http.get(BASE, () => { gets++; return HttpResponse.json(thread(card)) }),
      http.post(`${BASE}/proposals/${PROPOSAL}/confirm`, async ({ request }) => {
        sent = await request.json()
        csrf = request.headers.get("X-CSRF-Token")
        card = { ...CARD, status: "executing", can_confirm: false }
        return HttpResponse.json(card)
      }),
    )
    const article = await openCard()
    expect(within(article).getByText("2")).toBeInTheDocument()
    expect(within(article).getByText("10")).toBeInTheDocument()
    expect(within(article).getByRole("link", { name: "Edit" })).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
    expect(screen.getByText(/Typing “yes” does not approve/)).toBeInTheDocument()
    await userEvent.click(within(article).getByRole("button", { name: "Confirm" }))
    await waitFor(() => expect(sent).toEqual({ revision_id: CARD.revision_id, payload_hash: CARD.payload_hash }))
    expect(csrf).toBe("csrf-test-token")
    expect(await within(article).findByText("Executing")).toBeInTheDocument()
    await waitFor(() => expect(gets).toBeGreaterThan(1))
    card = { ...card, status: "succeeded", result: { purchase_order_id: "po-12" } }
    expect(await within(article).findByRole("link", { name: "Purchase order created" })).toHaveAttribute("href", "/purchasing/purchase-orders/po-12")
    expect(within(article).getByText("Succeeded")).toBeInTheDocument()
    expect(within(article).getByText("Delivery: none")).toBeInTheDocument()
  })

  it.each([
    ["approvals.second_person_required", "Sent to the Approvals page for another approver."],
    ["approvals.chat_confirm_limit", "The daily chat confirmation limit was reached. Review this on the Approvals page."],
    ["approvals.not_authorized_to_approve", "You don't have permission to approve this action."],
    ["approvals.revision_changed", "This action has changed. Reload before confirming."],
    ["approvals.preview_changed", "This action has changed. Reload before confirming."],
    ["approvals.expired", "This proposal has expired."],
    ["approvals.not_pending", "This proposal is no longer waiting for approval."],
    ["purchasing.preview_changed", "This action has changed. Reload before confirming."],
    ["purchasing.supplier_inactive", "The supplier is inactive. Review this on the Approvals page."],
    ["purchasing.supplier_email_required", "The supplier needs an e-mail address before this can be sent."],
    ["purchasing.po_already_sent", "This purchase order was already sent."],
    ["purchasing.unavailable", "This action cannot be confirmed here. Review it on the Approvals page."],
  ])("explains refusal %s", async (reason, text) => {
    setup({ ...CARD, can_confirm: false, cannot_confirm_reason: reason })
    const article = await openCard()
    expect(within(article).getByText(text)).toBeInTheDocument()
    expect(within(article).queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument()
  })

  it("Not now collapses locally and allows reopening without mutation", async () => {
    setup()
    let posts = 0
    server.use(http.post(`${BASE}/proposals/${PROPOSAL}/confirm`, () => { posts++; return HttpResponse.json(CARD) }))
    const article = await openCard()
    await userEvent.click(within(article).getByRole("button", { name: "Not now" }))
    expect(within(article).queryByText("Quantity")).not.toBeInTheDocument()
    await userEvent.click(within(article).getByRole("button", { name: "Show action" }))
    expect(within(article).getByText("Quantity")).toBeInTheDocument()
    expect(posts).toBe(0)
  })

  it("reloads after a 409 without retrying confirmation", async () => {
    setup()
    let changed = false
    let posts = 0
    server.use(
      http.get(BASE, () => HttpResponse.json(thread(changed ? { ...CARD, revision_id: "revision-2", can_confirm: false, cannot_confirm_reason: "approvals.second_person_required" } : CARD))),
      http.post(`${BASE}/proposals/${PROPOSAL}/confirm`, () => { changed = true; posts++; return conflict("approvals.revision_changed") }),
    )
    const article = await openCard()
    await userEvent.click(within(article).getByRole("button", { name: "Confirm" }))
    expect(await screen.findByText("Sent to the Approvals page for another approver.")).toBeInTheDocument()
    expect(posts).toBe(1)
    expect(within(await screen.findByRole("article", { name: CARD.title })).queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument()
  })

  it.each([false, true])("confirms Stop before posting and handles too-late=%s", async (tooLate) => {
    setup({ ...CARD, status: "succeeded", can_confirm: false, delivery_state: "scheduled", result: { outbox_id: "mail-1" } })
    let stopped = false
    let posts = 0
    server.use(
      http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([{ id: "mail-1", status: stopped ? "cancelled" : "pending", not_before: new Date(Date.now() + 120000).toISOString(), channel: "email" }])),
      http.post("*/api/v1/notifications/outbox/mail-1/stop", () => { posts++; if (tooLate) return conflict("notifications.too_late_to_stop"); stopped = true; return HttpResponse.json({ id: "mail-1", status: "cancelled" }) }),
    )
    const article = await openCard()
    expect(await within(article).findByText(/Sending in 2:00|Sending in 1:59/)).toBeInTheDocument()
    await userEvent.click(within(article).getByRole("button", { name: "Stop" }))
    expect(posts).toBe(0)
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Stop this e-mail?" })).getByRole("button", { name: "Cancel" }))
    expect(posts).toBe(0)
    await userEvent.click(within(article).getByRole("button", { name: "Stop" }))
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Stop this e-mail?" })).getByRole("button", { name: "Stop e-mail" }))
    expect(await within(article).findByText(tooLate ? /Too late to stop/ : "Stopped")).toBeInTheDocument()
    expect(posts).toBe(1)
  })
})

const item = (kind: Schemas["WorkItem"]["kind"], extra: Partial<Schemas["WorkItem"]> = {}): Schemas["WorkItem"] => ({ id: kind, kind, title: `Waiting ${kind}`, status: "pending_review", created_at: new Date(Date.now() - 3600000).toISOString(), ...extra })
describe("AI operational queues", () => {
  it("shows roles, age, expiry, scheduled mail, supplier reply-by and pagination", async () => {
    mockMe()
    let cursor: string | null = null
    server.use(http.get("*/api/v1/ai/work-queue", ({ request }) => {
      cursor = new URL(request.url).searchParams.get("cursor")
      return HttpResponse.json({ items: cursor ? [item("proposal", { id: "older", title: "Older action" })] : [item("proposal", { proposal_id: PROPOSAL, waiting_on_roles: ["owner", "inventory_manager"], expires_at: NOW }), item("outbound_mail", { stop_until: new Date(Date.now() + 120000).toISOString(), status: "pending" }), item("supplier_quote", { quote_request_id: "quote-1", due_date: "2026-11-09" })], next_cursor: cursor ? null : "older+/=" })
    }))
    renderApp("/ai/work-queue")
    expect(await screen.findByRole("link", { name: "Waiting proposal" })).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
    expect(screen.getByText("Waiting on: Owner, Inventory manager")).toBeInTheDocument()
    expect(screen.getAllByText(/Age: 1h/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Expires/)).toBeInTheDocument()
    expect(screen.getByText(/Reply by: 2026-11-09/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Waiting supplier_quote" })).toHaveAttribute("href", "/purchasing/quote-requests/quote-1")
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Load more" }))
    expect(await screen.findByText("Older action")).toBeInTheDocument()
    expect(cursor).toBe("older+/=")
  })

  it("shows failures, unknown deliveries, recent fallbacks and pause", async () => {
    mockMe(meWith("org.settings.write"))
    server.use(http.get("*/api/v1/ai/exceptions", () => HttpResponse.json({ paused: true, next_cursor: null, items: [item("proposal", { status: "failed", failure_code: "purchasing.po_changed", proposal_id: PROPOSAL }), item("outbound_mail", { status: "unknown" }), item("outbound_mail", { id: "failed-mail", title: "Failed mail", status: "failed" }), item("autonomy_fallback", { reasons: ["cap_reached"] })] })))
    renderApp("/ai/exceptions")
    expect(await screen.findByText("The purchase order changed before execution.")).toBeInTheDocument()
    expect(screen.getByText("Unknown (check with the recipient)")).toBeInTheDocument()
    expect(screen.getByText("Delivery: Failed")).toBeInTheDocument()
    expect(screen.getByText("The daily limit was reached.")).toBeInTheDocument()
    expect(screen.getByText(/Autonomy fall-backs from the last 7 days/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Review Autonomy" })).toHaveAttribute("href", "/ai/autonomy")
  })

  it.each(["work-queue", "exceptions"])("shows %s empty state", async (page) => {
    mockMe()
    server.use(http.get(`*/api/v1/ai/${page}`, () => HttpResponse.json({ items: [], next_cursor: null, paused: false })))
    renderApp(`/ai/${page}`)
    expect(await screen.findByText(page === "work-queue" ? "Nothing is waiting." : "No exceptions to review.")).toBeInTheDocument()
  })

  it.each(["work-queue", "exceptions"])("hides %s without proposal-read and makes no request", async (page) => {
    mockMe({ ...ME, permissions: [] })
    let gets = 0
    server.use(http.get(`*/api/v1/ai/${page}`, () => { gets++; return HttpResponse.json({ items: [], next_cursor: null }) }))
    renderApp(`/ai/${page}`)
    expect(await screen.findByText("You don't have access to this AI queue.")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Work queue" })).not.toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Exceptions" })).not.toBeInTheDocument()
    expect(gets).toBe(0)
  })
})

describe("delivery and delay integration", () => {
  it.each([
    ["none", "Delivery: none"], ["scheduled", "Delivery: Scheduled"], ["sending", "Delivery: Sending"], ["delivered", "Delivery: Delivered"], ["failed", "Delivery: Failed"], ["unknown", "Unknown (check with the recipient)"], ["stopped", "Stopped"],
  ] as const)("shows %s separately from execution on cards", async (state, label) => {
    setup({ ...CARD, status: "succeeded", can_confirm: false, delivery_state: state })
    const article = await openCard()
    expect(within(article).getByText("Succeeded")).toBeInTheDocument()
    expect(within(article).getByText(label)).toBeInTheDocument()
  })

  it("does not turn typed yes into confirmation", async () => {
    setup()
    let confirms = 0
    let message: unknown
    server.use(
      http.post(`${BASE}/proposals/${PROPOSAL}/confirm`, () => { confirms++; return HttpResponse.json(CARD) }),
      http.post(`${BASE}/messages`, async ({ request }) => { message = await request.json(); return new HttpResponse('event: final\ndata: {"kind":"final","run_id":"run-1","text":"Use Confirm on the card."}\n\n', { headers: { "Content-Type": "text/event-stream" } }) }),
    )
    await openCard()
    await userEvent.type(screen.getByLabelText("Ask the assistant"), "yes")
    await userEvent.click(screen.getByRole("button", { name: "Ask" }))
    await waitFor(() => expect(message).toEqual({ content: "yes" }))
    expect(confirms).toBe(0)
  })

  it.each(["activity", "detail"])("shows scheduled delivery and Stop on %s", async (surface) => {
    mockMe()
    let stopped = false
    server.use(
      http.get("*/api/v1/ai/autonomy/activity", () => HttpResponse.json({ items: [{ id: "evaluation-1", created_at: NOW, proposal_id: PROPOSAL, title: "Send PO", action_type: "purchasing.send_po", status: "succeeded", delivery_state: "scheduled", outcome: "acted", reasons: [], rule_id: "rule-1", rule_version: 1, granted_by_name: "Morgan Example" }], next_cursor: null })),
      http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])),
      http.get(`*/api/v1/approvals/proposals/${PROPOSAL}`, () => HttpResponse.json({ id: PROPOSAL, title: "Send PO", action_type: "purchasing.send_po", origin_kind: "assistant", status: "succeeded", delivery_state: "scheduled", current_revision: { id: "revision-1", preview: CARD.preview, evidence: [], revision_no: 1 }, revisions: [], decisions: [], attempts: [], expires_at: NOW })),
      http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([{ id: "mail-1", status: stopped ? "cancelled" : "pending", not_before: new Date(Date.now() + 120000).toISOString(), channel: "email" }])),
      http.post("*/api/v1/notifications/outbox/mail-1/stop", () => { stopped = true; return HttpResponse.json({ id: "mail-1", status: "cancelled" }) }),
    )
    renderApp(surface === "activity" ? "/ai/activity" : `/approvals/${PROPOSAL}`)
    expect(await screen.findByText(/Sending in 2:00|Sending in 1:59/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Stop" }))
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Stop this e-mail?" })).getByRole("button", { name: "Stop e-mail" }))
    await waitFor(() => expect(screen.getByText("Stopped")).toBeInTheDocument())
    expect(stopped).toBe(true)
  })

  it("stops queued mail from Work queue and refreshes it", async () => {
    mockMe()
    let stopped = false
    let gets = 0
    server.use(
      http.get("*/api/v1/ai/work-queue", () => { gets++; return HttpResponse.json({ items: stopped ? [] : [item("outbound_mail", { status: "pending", stop_until: new Date(Date.now() + 120000).toISOString() })], next_cursor: null }) }),
      http.post("*/api/v1/notifications/outbox/outbound_mail/stop", () => { stopped = true; return HttpResponse.json({ id: "outbound_mail", status: "cancelled" }) }),
    )
    renderApp("/ai/work-queue")
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }))
    expect(stopped).toBe(false)
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop e-mail" }))
    expect(await screen.findByText("Nothing is waiting.")).toBeInTheDocument()
    expect(gets).toBeGreaterThan(1)
  })

  it("loads, validates and saves the versioned business send delay in seconds", async () => {
    mockMe(meWith("org.settings.write"))
    let sent: unknown
    server.use(
      http.get("*/api/v1/ai/autonomy/rules", () => HttpResponse.json({ items: [], paused: false })),
      http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings: { outbound_send_delay_seconds: 120, terminology: { customers: "clients" }, allow_self_approval: false }, version: 7 })),
      http.patch("*/api/v1/org/settings", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ ...(sent as object), version: 8 }) }),
    )
    renderApp("/ai/autonomy")
    expect(await screen.findByText("Delay before AI-sent e-mails")).toBeInTheDocument()
    const input = screen.getByLabelText("Delay (minutes)")
    expect(input).toHaveValue(2)
    await userEvent.clear(input)
    await userEvent.type(input, "16")
    expect(screen.getByRole("button", { name: "Save e-mail delay" })).toBeDisabled()
    await userEvent.clear(input)
    await userEvent.type(input, "15")
    await userEvent.click(screen.getByRole("button", { name: "Save e-mail delay" }))
    await waitFor(() => expect(sent).toEqual({ version: 7, settings: { outbound_send_delay_seconds: 900, terminology: { customers: "clients" }, allow_self_approval: false } }))
    expect(await screen.findByText("Saved")).toBeInTheDocument()
  })

  it("reloads the delay on settings conflict and supports zero delay", async () => {
    mockMe(meWith("org.settings.write"))
    let changed = false
    server.use(
      http.get("*/api/v1/ai/autonomy/rules", () => HttpResponse.json({ items: [], paused: false })),
      http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings: { outbound_send_delay_seconds: changed ? 0 : 120, terminology: {} }, version: changed ? 8 : 7 })),
      http.patch("*/api/v1/org/settings", () => { changed = true; return conflict("org.settings_version_conflict") }),
    )
    renderApp("/ai/autonomy")
    await userEvent.click(await screen.findByRole("button", { name: "Save e-mail delay" }))
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /Reload/ }))
    await waitFor(() => expect(screen.getByLabelText("Delay (minutes)")).toHaveValue(0))
  })
})

describe("delivery races and results", () => {
  it("keeps the too-late notice visible when refreshed mail has started sending", async () => {
    setup({ ...CARD, status: "succeeded", can_confirm: false, delivery_state: "scheduled" })
    toast.dismiss()
    let sending = false
    server.use(
      http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([{ id: "racing-mail", status: sending ? "sending" : "pending", not_before: new Date(Date.now() + 120000).toISOString(), channel: "email" }])),
      http.post("*/api/v1/notifications/outbox/racing-mail/stop", () => { sending = true; return conflict("notifications.too_late_to_stop") }),
    )
    const article = await openCard()
    await waitFor(() => expect(screen.queryByText(/Too late to stop/)).not.toBeInTheDocument())
    await userEvent.click(await within(article).findByRole("button", { name: "Stop" }))
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Stop this e-mail?" })).getByRole("button", { name: "Stop e-mail" }))
    await waitFor(() => expect(within(article).getByText("Delivery: Sending")).toBeInTheDocument())
    expect(screen.getByText(/Too late to stop/)).toBeInTheDocument()
    expect(within(article).queryByRole("button", { name: "Stop" })).not.toBeInTheDocument()
  })

  it("links the sent purchase order while showing delivery is still scheduled", async () => {
    setup({ ...CARD, action_type: "purchasing.send_po", status: "succeeded", can_confirm: false, delivery_state: "scheduled", result: { purchase_order_id: "po-12", outbox_id: "mail-1" } })
    server.use(http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([{ id: "mail-1", status: "pending", not_before: new Date(Date.now() + 120000).toISOString(), channel: "email" }])))
    const article = await openCard()
    expect(within(article).getByRole("link", { name: "Sent to supplier" })).toHaveAttribute("href", "/purchasing/purchase-orders/po-12")
    expect(within(article).getByText("Succeeded")).toBeInTheDocument()
    expect(within(article).getByText("Delivery: Scheduled")).toBeInTheDocument()
    expect(await within(article).findByRole("button", { name: "Stop" })).toBeInTheDocument()
    expect(within(article).queryByText("Delivery: Delivered")).not.toBeInTheDocument()
  })
})
