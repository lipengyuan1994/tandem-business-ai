// Minimal server-sent events parser for `fetch` streams (the browser EventSource cannot POST or send a CSRF header).
export interface SseMessage {
  event: string
  data: string
}

export interface SseParser {
  /** Feed decoded text; returns the events completed by it. */
  push(chunk: string): SseMessage[]
  /** End of stream: returns an event that was not closed by a blank line. */
  flush(): SseMessage[]
}

export function createSseParser(): SseParser {
  let buffer = ""
  let event = ""
  let data: string[] = []

  const dispatch = (out: SseMessage[]) => {
    if (data.length) out.push({ event: event || "message", data: data.join("\n") })
    event = ""
    data = []
  }
  const line = (text: string, out: SseMessage[]) => {
    if (text === "") return dispatch(out)
    if (text.startsWith(":")) return // comment / keep-alive
    const colon = text.indexOf(":")
    const field = colon === -1 ? text : text.slice(0, colon)
    let value = colon === -1 ? "" : text.slice(colon + 1)
    if (value.startsWith(" ")) value = value.slice(1)
    if (field === "event") event = value
    else if (field === "data") data.push(value)
  }

  return {
    push(chunk) {
      const out: SseMessage[] = []
      buffer += chunk
      // A trailing CR may be the first half of CRLF: wait for the next chunk before treating it as a line end.
      const held = buffer.endsWith("\r") ? "\r" : ""
      const parts = buffer.slice(0, buffer.length - held.length).split(/\r\n|\n|\r/)
      buffer = (parts.pop() ?? "") + held
      for (const part of parts) line(part, out)
      return out
    },
    flush() {
      const out: SseMessage[] = []
      if (buffer) line(buffer.replace(/\r$/, ""), out)
      buffer = ""
      dispatch(out)
      return out
    },
  }
}
