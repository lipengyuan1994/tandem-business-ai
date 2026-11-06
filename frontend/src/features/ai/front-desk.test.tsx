import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { Schemas } from "@/api/types"
import { ME, meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { mockStatus, PROPOSAL, THREAD } from "./testing"

const ARTICLE_ID = "00000000-0000-7000-8000-0000000000d1"
const NOW = "2026-11-05T17:00:00Z"
const GROUNDING = { verdict: "contradicted", claims: [
  { text: "Returns require a receipt.", verdict: "supported", source: `${ARTICLE_ID}@1` },
  { text: "Returns are always free.", verdict: "unsupported", source: "none" },
  { text: "The service takes five minutes.", verdict: "contradicted", source: "db_tool" },
] }
const reply = { conversation_id: "c1", body: "Example reply", citations: [{ article_id: ARTICLE_ID, version: 1 }], grounding: GROUNDING }
const revision = (payload: object) => ({ id: "revision-1", revision_no: 1, preview: [], evidence: [], payload, payload_hash: "a".repeat(64), created_at: NOW, created_by_kind: "assistant" })
const proposal = (payload: object) => ({ id: PROPOSAL, title: "Answer the warranty question", action_type: "conversations.send_reply", origin_kind: "assistant", status: "pending_review", delivery_state: "none", current_revision: revision(payload), revisions: [], decisions: [], attempts: [], expires_at: NOW, can_decide: false })
function mockEvidence(payload: object = reply) {
  server.use(
    http.get("*/api/v1/approvals/action-types", () => HttpResponse.json([])),
    http.get(`*/api/v1/approvals/proposals/${PROPOSAL}`, () => HttpResponse.json(proposal(payload))),
    http.get(`*/api/v1/knowledge/articles/${ARTICLE_ID}`, () => HttpResponse.json({ id: ARTICLE_ID, current_version: 2, title: "New returns title", versions: [{ version_no: 1, title: "Returns policy", body_markdown: "Bring a receipt", tags: [], created_at: NOW, published_at: NOW }, { version_no: 2, title: "New returns title", body_markdown: "Updated", tags: [], created_at: NOW, published_at: null }] })),
  )
}

describe("Front-desk grounding evidence", () => {
  it("shows exact historical article titles/versions, claim verdicts/sources and the overall verdict on proposal detail", async () => {
    mockMe(meWith("conversations.thread.read"))
    mockEvidence()
    renderApp(`/approvals/${PROPOSAL}`)
    const evidence = await screen.findByRole("region", { name: "Reply grounding" })
    expect((await within(evidence).findAllByRole("link", { name: "Returns policy · version 1" })).length).toBe(2)
    for (const link of within(evidence).getAllByRole("link", { name: "Returns policy · version 1" })) expect(link).toHaveAttribute("href", `/knowledge/${ARTICLE_ID}?version=1`)
    expect(within(evidence).queryByText(/New returns title/)).not.toBeInTheDocument()
    expect(within(evidence).getByText("Overall verdict:").parentElement).toHaveTextContent("Contradicted")
    expect(within(evidence).getByText("Returns require a receipt.").closest("li")).toHaveTextContent("Supported")
    expect(within(evidence).getByText("Returns are always free.").closest("li")).toHaveTextContent("Unsupported")
    expect(within(evidence).getByText("No supporting source")).toBeInTheDocument()
    expect(within(evidence).getByText("Verified business database facts")).toBeInTheDocument()
  })

  it.each([null, { verdict: "unavailable", claims: [] }, { verdict: "supported", claims: [] }])("distinguishes unavailable checks from supported no-claim replies: %j", async (grounding) => {
    mockMe()
    mockEvidence({ ...reply, citations: [], grounding })
    renderApp(`/approvals/${PROPOSAL}`)
    const evidence = await screen.findByRole("region", { name: "Reply grounding" })
    expect(within(evidence).getByText(grounding?.verdict === "supported" ? "Supported" : "Check unavailable")).toBeInTheDocument()
    expect(within(evidence).queryByText("No factual claims in this reply.") !== null).toBe(grounding?.verdict === "supported")
  })

  it("does not fetch articles without Knowledge read permission", async () => {
    mockMe()
    mockEvidence()
    let reads = 0
    server.use(http.get(`*/api/v1/knowledge/articles/${ARTICLE_ID}`, () => { reads++; return HttpResponse.json({}) }))
    renderApp(`/approvals/${PROPOSAL}`)
    await screen.findAllByText("Article version 1 (Knowledge access required)")
    expect(reads).toBe(0)
    expect(screen.queryByRole("link", { name: /Returns policy/ })).not.toBeInTheDocument()
  })

  it("renders grounding and citations on a saved reply action card", async () => {
    mockMe(meWith("ai.assistant.use", "conversations.thread.read"))
    mockStatus()
    mockEvidence()
    const card: Schemas["ChatCard"] = { id: PROPOSAL, title: "Answer the warranty question", action_type: "conversations.send_reply", preview: [], status: "pending_review", can_confirm: false, cannot_confirm_reason: "approvals.second_person_required", revision_id: "revision-1", payload_hash: "a".repeat(64), delivery_state: "none" }
    const thread = { id: THREAD, title: "Warranty", created_at: NOW, updated_at: NOW, messages: [{ id: "m1", role: "assistant", content: "Please review", proposals: [card], proposal_ids: [PROPOSAL], run_id: null, created_at: NOW }] }
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([thread])),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json(thread)),
    )
    renderApp("/approvals")
    await userEvent.click(await screen.findByRole("button", { name: "Assistant" }))
    await userEvent.click(await screen.findByRole("button", { name: "Warranty" }))
    const article = await screen.findByRole("article", { name: card.title })
    expect(await within(article).findByText("Returns require a receipt.")).toBeInTheDocument()
    expect((await within(article).findAllByRole("link", { name: "Returns policy · version 1" })).length).toBe(2)
  })
})

const rule: Schemas["RuleOut"] = { id: "rule-1", action_type: "conversations.send_reply", level: "act_notify", shadow: true, daily_cap: 10, active: true, version: 3, granted_by: ME.user.id, created_at: null, updated_at: null, summary: { would_act: 1, acted: 0, fell_back: 2 } }
describe("Front-desk Autonomy and Exceptions", () => {
  it.each([true, false])("shows the reply rule and fallbacks and gates save by reply permission (allowed=%s)", async (allowed) => {
    mockMe(meWith("org.settings.write", ...(allowed ? ["conversations.proposal.approve"] : ["inventory.proposal.approve"])))
    let saved: unknown
    server.use(
      http.get("*/api/v1/ai/autonomy/rules", () => HttpResponse.json({ paused: false, items: [rule] })),
      http.put("*/api/v1/ai/autonomy/rules", async ({ request }) => { saved = await request.json(); return HttpResponse.json({ ...rule, version: 4 }) }),
    )
    renderApp("/ai/autonomy")
    expect(await screen.findByText("Front-desk replies")).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "Act and notify" })).toBeChecked()
    expect(screen.getByRole("switch", { name: "Shadow mode" })).toBeChecked()
    expect(screen.getByLabelText("Daily limit")).toHaveValue("10")
    for (const reason of ["The reply is not grounded in its sources.", "A claim contradicts the available sources.", "The grounding check is unavailable.", "A person is handling this conversation.", "The reply is combined with a booking or another action."]) expect(screen.getByText(reason)).toBeInTheDocument()
    const button = screen.getByRole("button", { name: "Save" })
    if (allowed) {
      await userEvent.click(button)
      await waitFor(() => expect(saved).toEqual({ action_type: rule.action_type, level: "act_notify", shadow: true, daily_cap: 10, active: true, version: 3 }))
    } else expect(button).toBeDisabled()
  })

  it.each(["human_mode", "stale_reply"])("shows cancelled web replies and plain-language %s reasons from Exceptions", async (reason) => {
    mockMe()
    server.use(http.get("*/api/v1/ai/exceptions", () => HttpResponse.json({ paused: false, next_cursor: null, items: [{ id: "reply-1", kind: "web_reply", title: "Web-chat reply", status: "cancelled", created_at: NOW, proposal_id: PROPOSAL, failure_code: reason }] })))
    renderApp("/ai/exceptions")
    expect(await screen.findByText(reason === "human_mode" ? "Cancelled: A person is handling this conversation." : "Cancelled: A newer message or ownership change made this reply out of date.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Web-chat reply" })).toHaveAttribute("href", `/approvals/${PROPOSAL}`)
  })

  it("counts down and stops a withheld web reply in the Work queue", async () => {
    mockMe(meWith("conversations.proposal.approve"))
    let stopped = false
    server.use(
      http.get("*/api/v1/ai/work-queue", () => HttpResponse.json({ next_cursor: null, items: stopped ? [] : [{ id: "reply-1", kind: "web_reply", title: "Web-chat reply", status: "withheld", created_at: NOW, stop_until: new Date(Date.now() + 120000).toISOString(), proposal_id: PROPOSAL }] })),
      http.post("*/api/v1/conversations/messages/reply-1/stop", () => { stopped = true; return HttpResponse.json({ id: "reply-1", publication_state: "stopped" }) }),
    )
    renderApp("/ai/work-queue")
    expect(await screen.findByText(/Sending in 2:00|Sending in 1:59/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Stop" }))
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop reply" }))
    expect(await screen.findByText("Nothing is waiting.")).toBeInTheDocument()
    expect(stopped).toBe(true)
  })
})
