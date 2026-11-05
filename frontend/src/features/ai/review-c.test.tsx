import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import i18n from "@/app/i18n"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { aiErrorText } from "./text"
import { mockStatus, RUN, sseResponse, THREAD } from "./testing"

const copy = {
  thread_busy: "The assistant is already answering in this chat. Wait for that answer, then try again.",
  copilot_busy: "The assistant is busy with other requests. Wait a moment, then retry your message.",
  idempotency_key_reused: "This retry does not match the original request. Send it as a new request.",
  operation_in_progress: "Your request is still being processed. Wait a moment, then retry to check its result.",
  operation_interrupted: "This request stopped before the assistant started. Send your message again.",
  input_too_large: "There is too much information to process at once. Try a shorter message or a smaller task.",
  hold_expired: "The reserved time has expired. Ask the assistant to check availability again.",
}

describe("AI review C", () => {
  it.each(Object.entries(copy))("explains ai.%s in plain language", (code, message) => {
    expect(aiErrorText(i18n.t, `ai.${code}`)).toBe(message)
  })

  it("reuses the operation UUID on retry and gives a new identical message a fresh UUID", async () => {
    mockMe(meWith("ai.assistant.use", "approvals.proposal.read"))
    mockStatus()
    const keys: (string | null)[] = []
    const bodies: unknown[] = []
    server.use(
      http.get("*/api/v1/approvals/proposals", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.get("*/api/v1/ai/copilot/threads", () => HttpResponse.json([{ id: THREAD, title: "Example chat" }])),
      http.get(`*/api/v1/ai/copilot/threads/${THREAD}`, () => HttpResponse.json({ id: THREAD, title: "Example chat", messages: [] })),
      http.post(`*/api/v1/ai/copilot/threads/${THREAD}/messages`, async ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key"))
        bodies.push(await request.json())
        if (keys.length === 1) return HttpResponse.error() // the response may have been lost after a commit
        return sseResponse([["final", { run_id: RUN, text: "Saved answer." }]])
      }),
    )
    renderApp("/approvals")
    await userEvent.click(await screen.findByRole("button", { name: "Assistant" }))
    const panel = await screen.findByRole("dialog", { name: "Assistant" })
    await userEvent.click(await within(panel).findByRole("button", { name: "Example chat" }))
    const box = within(panel).getByLabelText("Ask the assistant")
    await userEvent.type(box, "Hello")
    await userEvent.click(within(panel).getByRole("button", { name: "Ask" }))
    await within(panel).findByRole("alert")
    expect(keys[0]).toMatch(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/)
    await userEvent.click(within(panel).getByRole("button", { name: "Retry message" }))
    await waitFor(() => expect(keys).toHaveLength(2))
    await waitFor(() => expect(within(panel).queryByRole("button", { name: "Retry message" })).not.toBeInTheDocument())
    await userEvent.type(box, "Hello")
    await userEvent.click(within(panel).getByRole("button", { name: "Ask" }))
    await waitFor(() => expect(keys).toHaveLength(3))
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).not.toBe(keys[0])
    expect(bodies).toEqual([{ content: "Hello" }, { content: "Hello" }, { content: "Hello" }])
  })
})
