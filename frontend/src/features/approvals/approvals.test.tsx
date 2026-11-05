import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { ProposalDetail } from "@/api/types"
import { mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const PID = "00000000-0000-7000-8000-0000000000p1".replace("p", "a")
const REV = {
  id: "00000000-0000-7000-8000-0000000000r1".replace("r", "c"), revision_no: 1, payload: { name: "Brake pads" },
  payload_hash: "a".repeat(64), summary: "Add test item", created_by_kind: "assistant", created_by: null, created_at: "2026-11-05T17:00:00Z",
  evidence: [{ kind: "message", ref_type: "message", ref_id: null, excerpt: "My rear brake is squeaking" }],
  preview: [{ label: "Appointment", before: null, after: "Tue 10 Nov 11:45 MST" }, { label: "Brake pads", before: "2 available", after: "1 available" }],
} as const

function detail(status: string): ProposalDetail {
  return {
    id: PID, action_type: "testing.noop_write", status, title: "Book brake service for Ada", origin_kind: "assistant", origin_user_id: null,
    conversation_id: null, current_revision_id: REV.id, expires_at: "2026-11-06T17:00:00Z", failure_code: null,
    created_at: "2026-11-05T17:00:00Z", updated_at: "2026-11-05T17:00:00Z", version: 1, can_decide: status === "pending_review",
    current_revision: REV, revisions: [REV], decisions: [], attempts: [],
  } as unknown as ProposalDetail
}

describe("proposal detail", () => {
  it("shows the preview diff and evidence, and approves the exact revision", async () => {
    mockMe()
    let sent: unknown = null
    let csrf: string | null = null
    server.use(
      http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])),
      http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([])),
      http.get(`*/api/v1/approvals/proposals/${PID}`, () => HttpResponse.json(detail("pending_review"))),
      http.post(`*/api/v1/approvals/proposals/${PID}/decisions`, async ({ request }) => {
        sent = await request.json()
        csrf = request.headers.get("X-CSRF-Token")
        return HttpResponse.json(detail("approved"))
      }),
    )
    renderApp(`/approvals/${PID}`)
    expect(await screen.findByRole("heading", { name: "Book brake service for Ada" })).toBeInTheDocument()
    expect(screen.getByText("AI proposed")).toBeInTheDocument()
    expect(screen.getByText("Tue 10 Nov 11:45 MST")).toBeInTheDocument()
    expect(screen.getByText("2 available")).toBeInTheDocument()
    expect(screen.getByText("My rear brake is squeaking")).toBeInTheDocument()
    const approve = screen.getByRole("button", { name: "Approve: Tue 10 Nov 11:45 MST · 1 available" })
    await userEvent.click(approve)
    await waitFor(() => expect(sent).toEqual({ decision: "approve", revision_id: REV.id, payload_hash: REV.payload_hash }))
    expect(csrf).toBe("csrf-test-token")
    expect(await screen.findByText("Approved")).toBeInTheDocument()
  })

  it("names the autonomy rule and grantor for a policy approval with no human decider", async () => {
    mockMe()
    const proposal = detail("succeeded")
    proposal.decisions = [{ id: "decision-1", revision_id: REV.id, decision: "approve", decided_by: null, decided_by_kind: "policy", decided_via: "approvals", rule_id: "rule-1", rule_version: 3, granted_by: "grantor-1", granted_by_name: "Morgan Example", decided_by_name: "", roles_at_decision: ["owner"], comment: null, decided_at: "2026-11-05T17:00:00Z" }]
    server.use(
      http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])),
      http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([])),
      http.get(`*/api/v1/approvals/proposals/${PID}`, () => HttpResponse.json(proposal)),
    )
    renderApp(`/approvals/${PID}`)
    expect(await screen.findByText("Approved by autonomy rule (v3), granted by Morgan Example")).toBeInTheDocument()
    expect(screen.queryByText(/by null/)).not.toBeInTheDocument()
  })

  it("reloads the proposal when the revision changed underneath the user", async () => {
    mockMe()
    let gets = 0
    server.use(
      http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])),
      http.get(`*/api/v1/approvals/proposals/${PID}`, () => {
        gets += 1
        return HttpResponse.json(detail("pending_review"))
      }),
      http.post(`*/api/v1/approvals/proposals/${PID}/decisions`, () =>
        HttpResponse.json({ code: "approvals.revision_changed", status: 409, title: "Conflict" }, { status: 409 })),
    )
    renderApp(`/approvals/${PID}`)
    await userEvent.click(await screen.findByRole("button", { name: /^Approve:/ }))
    expect(await screen.findByText(/edited since you opened it/)).toBeInTheDocument()
    await waitFor(() => expect(gets).toBe(2))
  })
})
