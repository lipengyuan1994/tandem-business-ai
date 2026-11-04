// Test helpers for the AI screens. The AI endpoints are mocked with MSW (the project's approach); nothing here calls a model.
import { http, HttpResponse } from "msw"
import type { AiStatus, StepEvent } from "./types"
import { server } from "@/test/server"

export const RUN = "00000000-0000-7000-8000-0000000000a1"
export const THREAD = "00000000-0000-7000-8000-0000000000b1"
export const PROPOSAL = "00000000-0000-7000-8000-0000000000c1"

export const ON: AiStatus = { enabled: true, reason: null, policy: "hosted_vultr", budget_percent: 10 }

export function mockStatus(status: AiStatus = ON) {
  server.use(http.get("*/api/v1/ai/status", () => HttpResponse.json(status)))
}

/** Fill every nullable field, matching the backend's SSE JSON payload. */
export function stepEvent(event: Pick<StepEvent, "kind" | "run_id"> & Partial<StepEvent>): StepEvent {
  return { tool: null, status_text: null, proposal_id: null, proposal_title: null, text: null, error_code: null, ...event }
}

/** A text/event-stream response, cut into small pieces so that lines and events are split across chunks. */
export function sseResponse(events: [StepEvent["kind"], Pick<StepEvent, "run_id"> & Partial<Omit<StepEvent, "kind">>][], options: { piece?: number; keepOpen?: boolean; onCancel?: () => void } = {}) {
  const text = events.map(([kind, data]) => `event: ${kind}\ndata: ${JSON.stringify(stepEvent({ kind, ...data }))}\n\n`).join("")
  const bytes = new TextEncoder().encode(text)
  const piece = options.piece ?? 29
  return new HttpResponse(new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += piece) controller.enqueue(bytes.slice(i, i + piece))
      if (!options.keepOpen) controller.close()
    },
    cancel() { options.onCancel?.() },
  }), { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" } })
}
