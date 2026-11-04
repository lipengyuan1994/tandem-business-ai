import { describe, expect, it } from "vitest"
import { applyStepEvent, IDLE_STREAM, type CopilotStream } from "./stream"
import { createSseParser } from "./sse"
import { stepEvent } from "./testing"

describe("server-sent event parser", () => {
  it("splits events on blank lines and reads event and data fields", () => {
    const parser = createSseParser()
    const out = parser.push('event: step\ndata: {"a":1}\n\nevent: final\ndata: {"b":2}\n\n')
    expect(out).toEqual([{ event: "step", data: '{"a":1}' }, { event: "final", data: '{"b":2}' }])
  })

  it("keeps an event that arrives in pieces, even when a line is cut in half", () => {
    const parser = createSseParser()
    expect(parser.push("event: st")).toEqual([])
    expect(parser.push('ep\ndata: {"x"')).toEqual([])
    expect(parser.push(":1}\n")).toEqual([])
    expect(parser.push("\n")).toEqual([{ event: "step", data: '{"x":1}' }])
  })

  it("handles CRLF, a CR/LF pair split across chunks, comments and multi-line data", () => {
    const parser = createSseParser()
    expect(parser.push("event: step\r")).toEqual([])
    expect(parser.push(": keep-alive\r\ndata: one\r\ndata: two\r\n\r\n")).toEqual([{ event: "step", data: "one\ntwo" }])
  })

  it("ignores blank events, defaults the event name and flushes a last event without a blank line", () => {
    const parser = createSseParser()
    expect(parser.push("\n\ndata: lone\n\ndata: tail")).toEqual([{ event: "message", data: "lone" }])
    expect(parser.flush()).toEqual([{ event: "message", data: "tail" }])
  })
})

describe("copilot stream state", () => {
  const run = "00000000-0000-7000-8000-0000000000r1"
  it("collects steps, proposals and the final answer, then marks an error", () => {
    let s: CopilotStream = { ...IDLE_STREAM, status: "streaming", question: "Hi" }
    s = applyStepEvent(s, stepEvent({ kind: "step", run_id: run, tool: "find_slots", status_text: "Checking free times…" }))
    s = applyStepEvent(s, stepEvent({ kind: "step", run_id: run, tool: "get_customer" }))
    s = applyStepEvent(s, stepEvent({ kind: "proposal", run_id: run, proposal_id: "p1", proposal_title: "Book Ada" }))
    s = applyStepEvent(s, stepEvent({ kind: "proposal", run_id: run, proposal_id: "p1", proposal_title: "Book Ada" }))
    s = applyStepEvent(s, stepEvent({ kind: "final", run_id: run, text: "Done." }))
    expect(s.steps.map((x) => x.text)).toEqual(["Checking free times…", null])
    expect(s.proposals).toEqual([{ id: "p1", title: "Book Ada" }])
    expect(s.answer).toBe("Done.")
    const failed = applyStepEvent(s, stepEvent({ kind: "error", run_id: run, error_code: "ai.budget_exceeded" }))
    expect(failed.status).toBe("error")
    expect(failed.errorCode).toBe("ai.budget_exceeded")
  })
})
