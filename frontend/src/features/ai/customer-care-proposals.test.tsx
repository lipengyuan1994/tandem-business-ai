import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { ProposalDetail } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const PAYLOAD = { customer_id: "customer1", kind: "complaint_reply", subject: "About your repair", body: "A person will review your concern.", not_before: "2026-11-06T17:00:00Z", appointment_id: "appointment1", service_id: "service1", service_date: "2026-11-01", invoice_id: "invoice1", source_message_id: "message1", ai_generation: 5, due_month: "2026-11", review_requested: false }
const REV = { id: "revision1", revision_no: 1, payload: PAYLOAD, payload_hash: "a".repeat(64), summary: "Care reply", created_by_kind: "assistant" as const, created_by: null, created_at: "2026-11-06T17:00:00Z", evidence: [], preview: [{ label: "Customer Care", before: null, after: "Reply to the customer" }] }
const PROPOSAL: ProposalDetail = { id: "proposal1", action_type: "customer_care.send_message", status: "pending_review", title: "Care reply for River", origin_kind: "assistant", origin_user_id: null, conversation_id: "thread1", current_revision_id: REV.id, expires_at: "2026-11-10T17:00:00Z", failure_code: null, created_at: REV.created_at, updated_at: REV.created_at, version: 1, can_decide: true, delivery_state: "none", current_revision: REV, revisions: [REV], decisions: [], attempts: [] }
function setup(canDecide = true, kind = "complaint_reply") {
  mockMe(meWith("crm.customer.read"))
  const proposal = { ...PROPOSAL, can_decide: canDecide, current_revision: { ...REV, payload: { ...PAYLOAD, kind } } }
  server.use(http.get("*/api/v1/approvals/proposals/proposal1", () => HttpResponse.json(proposal)), http.get("*/api/v1/crm/customers/customer1", () => HttpResponse.json({ id: "customer1", display_name: "River Example" })), http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])), http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([])))
  return proposal
}

describe("Customer Care proposals", () => {
  it.each([["checkin", "Check-in"], ["review_request", "Review request"], ["recall", "Recall reminder"], ["complaint_reply", "Complaint reply"]])("renders %s and its send time and facts in Approvals", async (kind, label) => {
    setup(true, kind)
    renderApp("/approvals/proposal1")
    expect(await screen.findByText(label!)).toBeVisible()
    expect(await screen.findByRole("link", { name: "River Example" })).toHaveAttribute("href", "/customers/customer1")
    for (const value of ["About your repair", PAYLOAD.body, "Earliest send time", "6 Nov 2026 10:00 MST", "Facts used", "2026-11-01", "message1", "2026-11"]) expect(screen.getByText(value)).toBeVisible()
    expect(screen.getByRole("link", { name: "appointment1" })).toHaveAttribute("href", "/schedule?appointment=appointment1")
    if (kind === "complaint_reply") expect(screen.getByText("Needs a person")).toBeVisible()
    else expect(screen.queryByText("Needs a person")).not.toBeInTheDocument()
  })
  it.each(["approve", "reject"])("uses existing %s decisions with the exact revision", async (decision) => {
    const proposal = setup()
    let sent: unknown
    server.use(http.post("*/api/v1/approvals/proposals/proposal1/decisions", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ ...proposal, status: decision === "approve" ? "approved" : "rejected" }) }))
    renderApp("/approvals/proposal1")
    await screen.findByText("Complaint reply")
    if (decision === "approve") await userEvent.click(screen.getByRole("button", { name: /Approve:/ }))
    else {
      await userEvent.click(screen.getByRole("button", { name: "Reject" }))
      await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reject" }))
    }
    await waitFor(() => expect(sent).toMatchObject({ decision, revision_id: REV.id, payload_hash: REV.payload_hash }))
  })
  it("edits subject and body while preserving message facts and earliest send time", async () => {
    const proposal = setup()
    let sent: unknown
    server.use(http.post("*/api/v1/approvals/proposals/proposal1/revisions", async ({ request }) => { sent = await request.json(); return HttpResponse.json(proposal) }))
    renderApp("/approvals/proposal1")
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }))
    await userEvent.clear(screen.getByLabelText("Subject"))
    await userEvent.type(screen.getByLabelText("Subject"), "Your concern")
    await userEvent.clear(screen.getByLabelText("Message body"))
    await userEvent.type(screen.getByLabelText("Message body"), "Thank you. We will contact you.")
    await userEvent.click(screen.getByRole("button", { name: "Save as new revision" }))
    await waitFor(() => expect(sent).toEqual({ base_revision_id: REV.id, payload: { ...PAYLOAD, subject: "Your concern", body: "Thank you. We will contact you." } }))
  })
  it("hides approve, reject and edit when the server denies decision capability", async () => {
    setup(false)
    renderApp("/approvals/proposal1")
    await screen.findByText("Complaint reply")
    expect(screen.queryByRole("button", { name: /Approve:/ })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument()
  })
  it.each([false, true])("renders proposals from Work and case detail (case=%s)", async (inCase) => {
    setup()
    server.use(http.get("*/api/v1/ai/work-queue", () => HttpResponse.json({ items: [{ id: inCase ? "case1" : "proposal1", kind: inCase ? "customer_care_case" : "proposal", title: "Care reply", status: "open", action_type: inCase ? null : "customer_care.send_message", proposal_id: "proposal1", created_at: REV.created_at }], next_cursor: null })), http.get("*/api/v1/ai/customer-care/cases/case1", () => HttpResponse.json({ id: "case1", category: "complaint", priority: "high", status: "open", version: 1, customer_id: "customer1", conversation_id: "thread1", proposal_id: "proposal1" })))
    renderApp("/ai/work-queue")
    if (inCase) await userEvent.click(await screen.findByRole("button", { name: "View case" }))
    expect(await screen.findByText(PAYLOAD.subject)).toBeVisible()
    expect(screen.getByText("Needs a person")).toBeVisible()
    expect(screen.getByRole("link", { name: "Review suggested reply — approve, edit or reject" })).toHaveAttribute("href", "/approvals/proposal1")
  })
})
