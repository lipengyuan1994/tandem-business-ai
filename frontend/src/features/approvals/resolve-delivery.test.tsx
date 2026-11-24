import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import type { ProposalDetail } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const PID = "00000000-0000-7000-8000-0000000000a1"
const REV = {
  id: "00000000-0000-7000-8000-0000000000c1", revision_no: 1, payload: {}, payload_hash: "a".repeat(64), summary: "Send reminder", created_by_kind: "assistant",
  created_by: null, created_at: "2026-11-05T17:00:00Z", evidence: [], preview: [],
}
const PROPOSAL = {
  id: PID, action_type: "testing.noop_write", status: "succeeded", title: "Send reminder to Ada", origin_kind: "assistant", origin_user_id: null,
  conversation_id: null, current_revision_id: REV.id, expires_at: "2026-11-06T17:00:00Z", failure_code: null, created_at: "2026-11-05T17:00:00Z",
  updated_at: "2026-11-05T17:00:00Z", version: 1, can_decide: false, current_revision: REV, revisions: [REV], decisions: [], attempts: [],
} as unknown as ProposalDetail
const MAIL = { id: "o1", attempts: 3, channel: "email", created_at: "2026-11-05T17:00:00Z", delivered_at: null, last_error: "timeout", not_before: null, proposal_id: PID, provider_message_id: null, related_id: null, related_type: null, resolution: null, status: "unknown", template_key: "reminder" }

function setup(perms: string[]) {
  mockMe(meWith(...perms))
  let mail: Record<string, unknown> = { ...MAIL }
  const sent: unknown[] = []
  server.use(
    http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])),
    http.get(`*/api/v1/approvals/proposals/${PID}`, () => HttpResponse.json(PROPOSAL)),
    http.get("*/api/v1/notifications/outbox", () => HttpResponse.json([mail])),
    http.post("*/api/v1/notifications/outbox/:id/resolve", async ({ request }) => {
      const body = (await request.json()) as { resolution: string }
      sent.push(body)
      mail = { ...mail, status: body.resolution === "delivered" ? "delivered" : "pending", resolution: body.resolution }
      return HttpResponse.json(mail)
    }),
  )
  return sent
}

it("records that an uncertain message arrived, after confirming", async () => {
  const sent = setup(["notifications.outbox.resolve"])
  renderApp(`/approvals/${PID}`)
  await userEvent.click(await screen.findByRole("button", { name: "It arrived" }))
  await userEvent.click(within(await screen.findByRole("dialog", { name: "Mark as delivered?" })).getByRole("button", { name: "It arrived" }))
  await waitFor(() => expect(sent).toEqual([{ resolution: "delivered" }]))
  await waitFor(() => expect(screen.queryByRole("button", { name: "It arrived" })).not.toBeInTheDocument())
})

it("queues an uncertain message again and warns about duplicates", async () => {
  const sent = setup(["notifications.outbox.resolve"])
  renderApp(`/approvals/${PID}`)
  await userEvent.click(await screen.findByRole("button", { name: "Send again" }))
  const dialog = within(await screen.findByRole("dialog", { name: "Send this message again?" }))
  expect(dialog.getByText(/gets it twice/)).toBeInTheDocument()
  await userEvent.click(dialog.getByRole("button", { name: "Send again" }))
  await waitFor(() => expect(sent).toEqual([{ resolution: "retry" }]))
})

it("hides the resolve buttons without the outbox permission", async () => {
  setup([])
  renderApp(`/approvals/${PID}`)
  expect(await screen.findByText(/Delivery uncertain/)).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "It arrived" })).not.toBeInTheDocument()
})
