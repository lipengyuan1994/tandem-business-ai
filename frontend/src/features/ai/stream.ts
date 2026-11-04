// State of one streamed copilot answer, and the reader that turns a fetch response into step events.
import { createSseParser } from "./sse"
import type { StepEvent } from "./types"

export interface StreamStep {
  key: number
  /** Plain words from the server (`status_text`); null when the server sent none. */
  text: string | null
}

export interface CopilotStream {
  /** idle: nothing sent · streaming: waiting for events · done: finished, saved answer is shown from the thread · error. */
  status: "idle" | "streaming" | "done" | "error"
  question: string | null
  steps: StreamStep[]
  proposals: { id: string; title: string }[]
  answer: string | null
  errorCode: string | null
}

export const IDLE_STREAM: CopilotStream = { status: "idle", question: null, steps: [], proposals: [], answer: null, errorCode: null }

export function applyStepEvent(state: CopilotStream, event: StepEvent): CopilotStream {
  switch (event.kind) {
    case "step":
      return { ...state, steps: [...state.steps, { key: state.steps.length, text: event.status_text ?? null }] }
    case "proposal": {
      const id = event.proposal_id
      if (!id || state.proposals.some((p) => p.id === id)) return state
      return { ...state, proposals: [...state.proposals, { id, title: event.proposal_title ?? "" }] }
    }
    case "final":
      return { ...state, answer: event.text ?? "" }
    case "error":
      return { ...state, status: "error", errorCode: event.error_code ?? "ai.interrupted" }
    default:
      return state
  }
}

const KINDS = new Set(["step", "proposal", "final", "error"])

function toStepEvent(name: string, data: string): StepEvent | null {
  try {
    const parsed: unknown = JSON.parse(data)
    if (typeof parsed !== "object" || parsed === null) return null
    const event = parsed as Partial<StepEvent>
    const kind = event.kind ?? name
    if (!KINDS.has(kind)) return null
    return {
      kind: kind as StepEvent["kind"], run_id: event.run_id ?? "",
      tool: event.tool ?? null, status_text: event.status_text ?? null,
      proposal_id: event.proposal_id ?? null, proposal_title: event.proposal_title ?? null,
      text: event.text ?? null, error_code: event.error_code ?? null,
    }
  } catch {
    return null
  }
}

/** Reads a text/event-stream body to its end, calling `onEvent` for each step event. */
export async function readStepEvents(response: Response, onEvent: (event: StepEvent) => void): Promise<void> {
  if (!response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = createSseParser()
  const handle = (messages: { event: string; data: string }[]) => {
    for (const m of messages) {
      const event = toStepEvent(m.event, m.data)
      if (event) onEvent(event)
    }
  }
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      handle(parser.push(decoder.decode(value, { stream: true })))
    }
    handle(parser.push(decoder.decode()))
    handle(parser.flush())
  } finally {
    // Cancelling an already finished reader is a no-op; on abort it releases the connection.
    void reader.cancel().catch(() => undefined)
  }
}
