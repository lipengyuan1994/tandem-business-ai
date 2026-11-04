import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { ProposalDetail, Schemas, Thread, Today } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { mockStatus, ON, PROPOSAL, RUN } from "./testing"
import type { BriefingOut, ExtractOut, SummaryOut } from "./types"

const USE = "ai.assistant.use"
const NOW = "2026-11-05T17:00:00Z"

describe("summary in the inbox", () => {
  const TID = "00000000-0000-7000-8000-0000000000f1"
  const THREAD = {
    id: TID, channel: "web_chat", status: "waiting_on_staff", subject: null, customer_id: null, customer_name: null, visitor_name: "Ada", visitor_email: "ada@example.com",
    assigned_to: null, handoff_requested_at: null, last_message_at: NOW, version: 2, contact_consent_at: null, contact_consent_text: null,
    messages: [{ id: "m1", direction: "inbound", author_kind: "visitor", author_user_id: null, body: "My rear brake squeaks. Next Tuesday morning?", created_at: NOW, delivery_status: null, proposal_id: null }],
  } as Thread
  function mockInbox() {
    server.use(
      http.get("*/api/v1/conversations", () => HttpResponse.json({ next_cursor: null, items: [{ ...THREAD, last_message_preview: "My rear brake squeaks." }] })),
      http.get(`*/api/v1/conversations/${TID}`, () => HttpResponse.json(THREAD)),
    )
  }

  it("shows the summary, the open questions and a link to the reply draft", async () => {
    mockMe(meWith("conversations.thread.read", USE))
    mockStatus()
    mockInbox()
    let posted = false
    let csrf: string | null = null
    server.use(http.post(`*/api/v1/ai/conversations/${TID}/summary`, ({ request }) => {
      posted = true
      csrf = request.headers.get("X-CSRF-Token")
      return HttpResponse.json({ run_id: RUN, proposal_id: PROPOSAL, summary: { summary: "Ada wants a brake check next Tuesday morning.", open_questions: ["Which bike?", "Is 10:00 fine?"], suggested_reply: "Hi Ada, ..." } } satisfies SummaryOut)
    }))
    renderApp(`/inbox/${TID}`)
    const summarise = await screen.findByRole("button", { name: "Summarise" })
    await waitFor(() => expect(summarise).toBeEnabled())
    await userEvent.click(summarise)
    const dialog = await screen.findByRole("dialog", { name: "Conversation summary" })
    expect(await within(dialog).findByText("Ada wants a brake check next Tuesday morning.")).toBeInTheDocument()
    expect(within(dialog).getByText("Which bike?")).toBeInTheDocument()
    expect(within(dialog).getByText("Is 10:00 fine?")).toBeInTheDocument()
    expect(within(dialog).getByText(/Reply draft created — review in Approvals/)).toBeInTheDocument()
    expect(within(dialog).getByRole("link", { name: "Review the draft" })).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
    expect(posted).toBe(true)
    expect(csrf).toBe("csrf-test-token")
  })

  it("is not offered without the AI permission, and is switched off with the reason when AI is off", async () => {
    mockMe(meWith("conversations.thread.read"))
    mockInbox()
    const first = renderApp(`/inbox/${TID}`)
    await screen.findByText("My rear brake squeaks. Next Tuesday morning?")
    expect(screen.queryByRole("button", { name: "Summarise" })).not.toBeInTheDocument()
    first.unmount()

    mockMe(meWith("conversations.thread.read", USE))
    mockStatus({ ...ON, enabled: false, reason: "ai.disabled_by_policy" })
    renderApp(`/inbox/${TID}`)
    const button = await screen.findByRole("button", { name: "Summarise" })
    await waitFor(() => expect(button).toBeDisabled())
    expect(button).toHaveAttribute("title", expect.stringContaining("turned off by your privacy settings"))
  })

  it("explains a failed summary in plain words", async () => {
    mockMe(meWith("conversations.thread.read", USE))
    mockStatus()
    mockInbox()
    server.use(http.post(`*/api/v1/ai/conversations/${TID}/summary`, () => HttpResponse.json({ code: "ai.provider_unavailable", status: 503, title: "x" }, { status: 503 })))
    renderApp(`/inbox/${TID}`)
    const button = await screen.findByRole("button", { name: "Summarise" })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    expect(await screen.findByText(/The AI service did not answer/)).toBeInTheDocument()
  })
})

describe("AI badge and evidence in Approvals", () => {
  const PID = "00000000-0000-7000-8000-0000000000a9"
  const REV = {
    id: "00000000-0000-7000-8000-0000000000c9", revision_no: 1, payload: { name: "x" }, payload_hash: "a".repeat(64), summary: "x", created_by_kind: "assistant", created_by: null, created_at: NOW,
    evidence: [{ kind: "message", ref_type: "message", ref_id: null, excerpt: "My rear brake is squeaking" }], preview: [{ label: "Appointment", before: null, after: "Tue 10 Nov 11:45 MST" }],
  }
  const proposal = (origin: string, id = PID) => ({
    id, action_type: "testing.noop_write", status: "pending_review", title: origin === "assistant" ? "Book brake service for Ada" : "Reply to Ben", origin_kind: origin, origin_user_id: null, conversation_id: null,
    current_revision_id: REV.id, expires_at: "2026-11-06T17:00:00Z", failure_code: null, created_at: NOW, updated_at: NOW, version: 1, can_decide: true, current_revision: REV, revisions: [REV], decisions: [], attempts: [],
  })

  it("marks only assistant proposals in the list", async () => {
    mockMe()
    server.use(http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [proposal("assistant"), proposal("staff", "other")], next_cursor: null })))
    renderApp("/approvals")
    const ai = (await screen.findByText("Book brake service for Ada")).closest("tr")!
    expect(within(ai).getByText("AI proposed")).toBeInTheDocument()
    expect(within(screen.getByText("Reply to Ben").closest("tr")!).queryByText("AI proposed")).not.toBeInTheDocument()
  })

  it("shows the badge and an Evidence list on an assistant proposal, and neither on a staff one", async () => {
    mockMe()
    server.use(
      http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])),
      http.get(`*/api/v1/approvals/proposals/${PID}`, () => HttpResponse.json(proposal("assistant") as unknown as ProposalDetail)),
      http.get("*/api/v1/approvals/proposals/staff-one", () => HttpResponse.json(proposal("staff", "staff-one") as unknown as ProposalDetail)),
    )
    const first = renderApp(`/approvals/${PID}`)
    expect(await screen.findByText("AI proposed")).toBeInTheDocument()
    const evidence = screen.getByText("Evidence").closest("[data-slot=card]") as HTMLElement
    expect(within(evidence).getByText("My rear brake is squeaking")).toBeInTheDocument()
    first.unmount()
    renderApp("/approvals/staff-one")
    expect(await screen.findByText("Why this was proposed")).toBeInTheDocument()
    expect(screen.queryByText("AI proposed")).not.toBeInTheDocument()
    expect(screen.queryByText("Evidence")).not.toBeInTheDocument()
  })
})

describe("briefing on Today", () => {
  const today: Today = { local_date: "2026-11-10", timezone: "America/Denver", agenda: null, approvals: null, conversations: { waiting_on_staff: 2 }, delivery_issues: null, low_stock: null }
  const base = () => server.use(http.get("*/api/v1/dashboard/today", () => HttpResponse.json(today)))

  it("shows the bullets when AI is on", async () => {
    mockMe(meWith("conversations.thread.read", USE))
    mockStatus()
    base()
    server.use(http.get("*/api/v1/ai/briefing", () => HttpResponse.json({ generated_at: "2026-11-10T14:05:00Z", bullets: ["Two messages are waiting for a reply.", "Brake pads are below the reorder point."] } satisfies BriefingOut)))
    renderApp("/today")
    expect(await screen.findByText("Today's briefing")).toBeInTheDocument()
    expect(await screen.findByText("Two messages are waiting for a reply.")).toBeInTheDocument()
    expect(screen.getByText("Brake pads are below the reorder point.")).toBeInTheDocument()
    expect(screen.getByText(/Written at 07:05/)).toBeInTheDocument() // business time (Denver), not UTC
  })

  it("is hidden when AI is off and never asks for the briefing", async () => {
    mockMe(meWith("conversations.thread.read", USE))
    mockStatus({ ...ON, enabled: false, reason: "ai.not_configured" })
    base()
    let asked = false
    server.use(http.get("*/api/v1/ai/briefing", () => { asked = true; return HttpResponse.json({ generated_at: NOW, bullets: ["x"] }) }))
    renderApp("/today")
    await screen.findByText("Inbox")
    await waitFor(() => expect(screen.queryByText("Today's briefing")).not.toBeInTheDocument())
    expect(asked).toBe(false)
  })

  it("is hidden for people without the AI permission", async () => {
    mockMe(meWith("conversations.thread.read"))
    base()
    renderApp("/today")
    await screen.findByText("Inbox")
    expect(screen.queryByText("Today's briefing")).not.toBeInTheDocument()
  })

  it("says plainly when the briefing could not be written", async () => {
    mockMe(meWith(USE))
    mockStatus()
    base()
    server.use(http.get("*/api/v1/ai/briefing", () => HttpResponse.json({ code: "ai.budget_exceeded", status: 429, title: "x" }, { status: 429 })))
    renderApp("/today")
    expect(await screen.findByText(/Today's AI limit has been used up/)).toBeInTheDocument()
  })
})

describe("paste supplier reply", () => {
  const ROOT = "*/api/v1/purchasing"
  const request: Schemas["QuoteRequestOut"] = {
    id: "request-1", status: "sent", needed_by: "2099-11-20", reply_by: "2099-11-10", message: null, version: 1, created_by: null, created_at: "2099-11-01T12:00:00Z",
    lines: [{ item_id: "item-1", name: "Example pads", quantity: "2" }, { item_id: "item-2", name: "Example rotors", quantity: "1" }],
    suppliers: [
      { id: "invite-1", supplier_id: "supplier-1", supplier_name: "Example Parts", status: "invited", sent_at: null, responded_at: null, last_followup_at: null },
      { id: "invite-2", supplier_id: "supplier-2", supplier_name: "Example Backup", status: "invited", sent_at: null, responded_at: null, last_followup_at: null },
    ], quotes: [],
  }
  function setup(extra: string[] = [USE, "purchasing.po.write"]) {
    mockMe(meWith("purchasing.po.read", ...extra))
    mockStatus()
    server.use(
      http.get(`${ROOT}/quote-requests/request-1`, () => HttpResponse.json(request)),
      http.get(`${ROOT}/quote-requests/request-1/comparison`, () => HttpResponse.json({ quote_request_id: "request-1", as_of: "2099-11-01", ranked: [], ineligible: [] })),
    )
  }
  const reply = "Pads 11.40 each, rotors twelve fifty. Delivery in 4 days."

  it("marks the values that could not be found in the text and drafts nothing", async () => {
    setup()
    let body: unknown = null
    const out: ExtractOut = {
      run_id: RUN, verified: false, proposal_id: null,
      extract: { lines: [{ item_id: "item-1", unit_price: "11.40", quantity_available: null }, { item_id: "item-2", unit_price: null, quantity_available: null }], currency: "USD", lead_time_days: 4, min_quantity: null, valid_until: null, notes: "" },
      unverified_fields: ["lines.1.unit_price"],
    }
    server.use(http.post(`${ROOT.replace("purchasing", "ai")}/procurement/quote-requests/request-1/extract`, async ({ request: r }) => { body = await r.json(); return HttpResponse.json(out) }))
    renderApp("/purchasing/quote-requests/request-1")
    await userEvent.click(await screen.findByRole("button", { name: "Paste supplier reply" }))
    const dialog = await screen.findByRole("dialog", { name: "Read a supplier's reply" })
    const read = within(dialog).getByRole("button", { name: "Read the reply" })
    expect(read).toBeDisabled() // needs a supplier and some text
    await userEvent.selectOptions(within(dialog).getByLabelText("Which supplier wrote this?"), "supplier-1")
    await userEvent.click(within(dialog).getByLabelText("Supplier's reply"))
    await userEvent.paste(reply)
    expect(within(dialog).getByText(`${reply.length} of 8000 characters`)).toBeInTheDocument()
    await userEvent.click(read)
    await waitFor(() => expect(body).toEqual({ supplier_id: "supplier-1", text: reply }))

    expect(await within(dialog).findByText("Some numbers could not be found in the text; enter this quote by hand.")).toBeInTheDocument()
    const rotors = within(dialog).getByText("Example rotors").closest("li")!
    expect(within(rotors).getByText("Not found in the text")).toBeInTheDocument()
    const pads = within(dialog).getByText("Example pads").closest("li")!
    expect(within(pads).getByText("11.40")).toBeInTheDocument()
    expect(within(pads).queryByText("Not found in the text")).not.toBeInTheDocument()
    expect(within(dialog).getByText("Example rotors: price")).toBeInTheDocument() // the plain list of what could not be checked
    expect(within(dialog).getByText("4 days")).toBeInTheDocument()
    expect(within(dialog).queryByRole("link", { name: "Open the approval" })).not.toBeInTheDocument()
  })

  it("links to the proposal when every number was found", async () => {
    setup()
    server.use(http.post("*/api/v1/ai/procurement/quote-requests/request-1/extract", () => HttpResponse.json({
      run_id: RUN, verified: true, proposal_id: PROPOSAL, unverified_fields: [],
      extract: { lines: [{ item_id: "item-1", unit_price: "11.40" }, { item_id: "item-2", unit_price: "12.50" }], currency: "USD", lead_time_days: 4 },
    } satisfies ExtractOut)))
    renderApp("/purchasing/quote-requests/request-1")
    await userEvent.click(await screen.findByRole("button", { name: "Paste supplier reply" }))
    const dialog = await screen.findByRole("dialog", { name: "Read a supplier's reply" })
    await userEvent.selectOptions(within(dialog).getByLabelText("Which supplier wrote this?"), "supplier-2")
    await userEvent.click(within(dialog).getByLabelText("Supplier's reply"))
    await userEvent.paste(reply)
    await userEvent.click(within(dialog).getByRole("button", { name: "Read the reply" }))
    expect(await within(dialog).findByText("Every number was found in the text you pasted.")).toBeInTheDocument()
    expect(within(dialog).getByRole("link", { name: "Open the approval" })).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
    expect(within(dialog).queryByText("Not found in the text")).not.toBeInTheDocument()
  })

  it("is not offered without both permissions", async () => {
    setup([USE])
    renderApp("/purchasing/quote-requests/request-1")
    await screen.findByText("Example Parts")
    expect(screen.queryByRole("button", { name: "Paste supplier reply" })).not.toBeInTheDocument()
  })
})

describe("ask AI to request quotes", () => {
  const PADS = "00000000-0000-7000-8000-0000000000d1"
  const LOC = "00000000-0000-7000-8000-0000000000e1"
  const level = { item_id: PADS, item_name: "Brake pads (pair)", sku: "BP-1", unit: "each", stock_location_id: LOC, on_hand: "3.000", reserved: "2.000", available: "1.000", reorder_point: "2.000", below_reorder: true }
  const detail = (available: string) => ({ levels: [{ ...level, available }], movements: [], reservations: [], rule: { item_id: PADS, stock_location_id: LOC, reorder_point: "2.000", target_level: "6.000", lead_time_days: 3, supplier_hint: null } })
  const supplier = (n: number, email: string | null = `s${n}@example.com`) => ({ id: `supplier-${n}`, name: `Supplier ${n}`, email, items: [{ catalog_item_id: PADS }] })
  let supplierCalls = 0
  function setup(suppliers: unknown[], available = "1.000") {
    supplierCalls = 0
    mockMe(meWith("inventory.stock.read", "purchasing.po.read", "purchasing.po.write", USE))
    mockStatus()
    server.use(
      http.get(`*/api/v1/inventory/items/${PADS}`, () => HttpResponse.json(detail(available))),
      http.get("*/api/v1/purchasing/suppliers", () => { supplierCalls += 1; return HttpResponse.json({ items: suppliers, next_cursor: null }) }),
    )
  }

  it("drafts the request and links to the proposals to review", async () => {
    setup([supplier(1), supplier(2), supplier(3, null)])
    let asked = 0
    server.use(http.post(`*/api/v1/ai/procurement/items/${PADS}/request-quotes`, () => { asked += 1; return HttpResponse.json({ run_id: RUN, proposal_ids: [PROPOSAL] }) }))
    renderApp(`/inventory/${PADS}`)
    expect(await screen.findByText(/2 suppliers sell this/)).toBeInTheDocument() // the one without an e-mail address is not counted
    const button = screen.getByRole("button", { name: "Ask AI to request quotes" })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    expect(await screen.findByText(/Quote request drafted — review in Approvals/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Review proposal 1" })).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
    expect(asked).toBe(1)
  })

  it("is hidden with fewer than two suppliers or when stock is not low", async () => {
    setup([supplier(1)])
    const first = renderApp(`/inventory/${PADS}`)
    await screen.findByRole("heading", { name: "Brake pads (pair)" })
    await waitFor(() => expect(supplierCalls).toBe(1)) // the supplier list arrived and still one supplier is too few
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.queryByRole("button", { name: "Ask AI to request quotes" })).not.toBeInTheDocument()
    first.unmount()

    setup([supplier(1), supplier(2)], "5.000")
    renderApp(`/inventory/${PADS}`)
    await screen.findByRole("heading", { name: "Brake pads (pair)" })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.queryByRole("button", { name: "Ask AI to request quotes" })).not.toBeInTheDocument()
    expect(supplierCalls).toBe(0) // stock is fine: nothing to look up
  })
})
