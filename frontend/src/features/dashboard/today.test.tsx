import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { ProposalDetail, Today } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const AGENDA = [{
  id: "a1", customer_id: "c1", customer_name: "Ada Example", service_id: "s1", service_name: "Brake service", resource_id: "r1", resource_name: "Sam (technician)",
  location_id: "l1", starts_at: "2026-11-10T18:45:00Z", ends_at: "2026-11-10T20:15:00Z", blocked_until: "2026-11-10T20:30:00Z", timezone: "America/Denver",
  status: "confirmed", hold_expires_at: null, proposal_id: null, source: "staff", notes: null, cancelled_reason: null, version: 1,
}]

describe("today", () => {
  it("shows only the sections the API returned for this user", async () => {
    mockMe(meWith("scheduling.appointment.read"))
    const today: Today = { local_date: "2026-11-10", timezone: "America/Denver", agenda: AGENDA as Today["agenda"], approvals: null, conversations: null, delivery_issues: null, low_stock: null }
    server.use(http.get("*/api/v1/dashboard/today", () => HttpResponse.json(today)))
    renderApp("/today")
    expect(await screen.findByText("Today's agenda")).toBeInTheDocument()
    expect(screen.getByText("11:45")).toBeInTheDocument()
    expect(screen.getByText("Brake service · Ada Example")).toBeInTheDocument()
    expect(screen.queryByText(/Approvals you can decide/)).not.toBeInTheDocument()
    expect(screen.queryByText("Reorder suggestions")).not.toBeInTheDocument()
    expect(screen.queryByText("Late purchase orders")).not.toBeInTheDocument()
  })

  it("shows approvals, inbox counts, delivery issues and low stock when permitted", async () => {
    mockMe(meWith("conversations.thread.read", "inventory.reorder.read"))
    server.use(http.get("*/api/v1/dashboard/today", () => HttpResponse.json({
      local_date: "2026-11-10", timezone: "America/Denver", agenda: null,
      approvals: { can_decide: 1, top: [{ id: "p1", title: "Book Ada's brake service", action_type: "scheduling.book_appointment", origin_kind: "user", created_at: "2026-11-05T17:00:00Z" }] },
      conversations: { waiting_on_staff: 2 }, delivery_issues: 1,
      low_stock: [{ item_id: "i1", item_name: "Brake pads (pair)", available: "1.000", reorder_point: "2.000", target_level: "6.000", suggested: "5.000", explanation: "Below 2" }],
    })))
    renderApp("/today")
    expect(await screen.findByText("Approvals you can decide (1)")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Book Ada's brake service" })).toHaveAttribute("href", "/approvals/p1")
    expect(screen.getByText("1 messages may not have arrived")).toBeInTheDocument()
    expect(await screen.findByText("Brake pads (pair)")).toBeInTheDocument()
  })
  it("counts all late orders and links to the purchase-order filter", async () => {
    mockMe(meWith("purchasing.po.read"))
    server.use(
      http.get("*/api/v1/dashboard/today", () => HttpResponse.json({ local_date: "2026-11-10", timezone: "America/Denver" })),
      http.get("*/api/v1/purchasing/late-purchase-orders", () => HttpResponse.json([{ id: "late-1" }, { id: "late-2" }])),
    )
    renderApp("/today")
    expect(await screen.findByText("2 purchase orders are late")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "View late orders" })).toHaveAttribute("href", "/purchasing?late=true")
  })

})

const PID = "00000000-0000-7000-8000-0000000000a9"
function proposal(action_type: string, payload: Record<string, unknown>): ProposalDetail {
  const rev = { id: "rev1", revision_no: 1, payload, payload_hash: "a".repeat(64), summary: "x", created_by_kind: "user", created_by: null, created_at: "2026-11-05T17:00:00Z", evidence: [], preview: [{ label: "Reply", before: null, after: "Hi" }] }
  return { id: PID, action_type, status: "pending_review", title: "Proposal", origin_kind: "user", origin_user_id: null, conversation_id: null, current_revision_id: rev.id,
    expires_at: "2026-11-06T17:00:00Z", failure_code: null, created_at: "2026-11-05T17:00:00Z", updated_at: "2026-11-05T17:00:00Z", version: 1, can_decide: true,
    current_revision: rev, revisions: [rev], decisions: [], attempts: [] } as unknown as ProposalDetail
}

function mockProposal(p: ProposalDetail) {
  const revisions: unknown[] = []
  server.use(
    http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])),
    http.get(`*/api/v1/approvals/proposals/${PID}`, () => HttpResponse.json(p)),
    http.post(`*/api/v1/approvals/proposals/${PID}/revisions`, async ({ request }) => { revisions.push(await request.json()); return HttpResponse.json(p, { status: 201 }) }),
  )
  return revisions
}

describe("action-specific approval forms", () => {
  it("edits a reply as text", async () => {
    mockMe()
    const revisions = mockProposal(proposal("conversations.send_reply", { conversation_id: "c1", body: "Hi" }))
    renderApp(`/approvals/${PID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }))
    const text = screen.getByLabelText("Reply text")
    await userEvent.clear(text)
    await userEvent.type(text, "Hi Ada, Tuesday 11:45 works.")
    await userEvent.click(screen.getByRole("button", { name: "Save as new revision" }))
    await waitFor(() => expect(revisions).toEqual([{ base_revision_id: "rev1", payload: { conversation_id: "c1", body: "Hi Ada, Tuesday 11:45 works." } }]))
  })

  it("picks another slot for a booking and keeps the rest of the payload", async () => {
    mockMe(meWith("catalog.item.read", "scheduling.appointment.read"))
    const payload = { customer: { id: "c1" }, service_id: "s1", resource_id: "r1", starts_at: "2026-11-10T18:45:00Z", hold_id: "h1", reply: { conversation_id: "conv1", body: "Booked!" } }
    const revisions = mockProposal(proposal("scheduling.book_appointment", payload))
    const slotQueries: string[] = []
    server.use(
      http.get("*/api/v1/catalog/items", () => HttpResponse.json({ next_cursor: null, items: [{ id: "s1", kind: "service", name: "Brake service", status: "active", sku: null, description: null, unit: "each", currency: "USD", price_minor: null, duration_minutes: 90, buffer_minutes: 0, resource_kind: "staff", is_stock_tracked: false, is_bookable_online: true, version: 1 }] })),
      http.get("*/api/v1/scheduling/slots", ({ request }) => {
        slotQueries.push(new URL(request.url).searchParams.get("earliest") ?? "")
        return HttpResponse.json([{ starts_at: "2026-11-10T21:00:00Z", ends_at: "2026-11-10T22:30:00Z", resource_id: "r2", resource_name: "Alex", local_label: "Tue 10 Nov 14:00 MST" }])
      }),
    )
    renderApp(`/approvals/${PID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }))
    await userEvent.click(await screen.findByRole("button", { name: "Tue 10 Nov 14:00 MST · Alex" }))
    expect(slotQueries[0]).toBe("2026-11-10T07:00:00.000Z") // the proposal's local day in Denver
    await waitFor(() => expect(revisions).toEqual([{ base_revision_id: "rev1", payload: { ...payload, resource_id: "r2", starts_at: "2026-11-10T21:00:00Z", hold_id: null } }]))
  })

  it("edits a stock adjustment's quantity and reason", async () => {
    mockMe()
    const revisions = mockProposal(proposal("inventory.adjust_stock", { item_id: "i1", quantity: "2", reason: "receipt", note: null }))
    renderApp(`/approvals/${PID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }))
    const qty = screen.getByLabelText("Change (negative for a loss)")
    await userEvent.clear(qty)
    await userEvent.type(qty, "-1")
    await userEvent.selectOptions(screen.getByLabelText("Reason"), "adjustment_loss")
    await userEvent.click(screen.getByRole("button", { name: "Save as new revision" }))
    await waitFor(() => expect(revisions).toEqual([{ base_revision_id: "rev1", payload: { item_id: "i1", quantity: "-1", reason: "adjustment_loss", note: null } }]))
  })
})
