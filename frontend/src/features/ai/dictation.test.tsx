import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { useState } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { setCsrfToken } from "@/api/client"
import { Textarea } from "@/components/ui/textarea"
import { renderWithSession } from "@/test/render"
import { server } from "@/test/server"
import { mockStatus, ON, THREAD } from "./testing"
import { dictationMimeType } from "./useDictation"
import type { DictationOptions, DictationResult } from "./dictation-api"

class RecorderMock {
  static instances: RecorderMock[] = []
  static isTypeSupported = vi.fn((type: string) => type === "audio/webm;codecs=opus")
  state = "inactive"
  mimeType: string
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null
  start = vi.fn(() => { this.state = "recording" })
  stop = vi.fn(() => {
    this.state = "inactive"
    this.ondataavailable?.({ data: new Blob(["synthetic audio"], { type: this.mimeType }) })
    this.onstop?.()
  })
  constructor(_stream: MediaStream, options: MediaRecorderOptions) {
    this.mimeType = options.mimeType!
    RecorderMock.instances.push(this)
  }
}
const stopTrack = vi.fn()
const getUserMedia = vi.fn()
const response: DictationResult = { text: "Hello there.", raw_text: "um hello there", polished: true, duration_s: 2, warnings: [] }
const submit = vi.fn()
let requests: Request[] = []
function Harness({ options = {}, maxLength }: { options?: DictationOptions; maxLength?: number }) {
  const [value, setValue] = useState("Before after")
  return <form onSubmit={(event) => { event.preventDefault(); submit(value) }}>
    <label htmlFor="dictation-test">Message</label>
    <Textarea id="dictation-test" dictation={options} value={value} maxLength={maxLength} onChange={(event) => setValue(event.target.value)} />
    <button type="submit">Send</button><output data-testid="value">{value}</output>
  </form>
}
function mount(options: DictationOptions = {}, permissions = ["ai.assistant.use"], maxLength?: number) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const view = renderWithSession(<QueryClientProvider client={client}><Harness options={options} maxLength={maxLength} /></QueryClientProvider>, permissions)
  return { ...view, client }
}
const field = () => screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message" })
const startButton = () => screen.findByRole("button", { name: "Start dictation" })
async function start() {
  fireEvent.click(await startButton())
  await screen.findByRole("button", { name: "Stop dictation" })
}
async function finish() {
  fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
  await screen.findByText("Inserted")
}
function fail(code: string, status = 422, reason?: string) {
  server.use(http.post("*/api/v1/voice/transcriptions", () => HttpResponse.json({ code, status, title: "Voice error", ...(reason ? { reason } : {}) }, { status })))
}
beforeEach(async () => {
  requests = []
  RecorderMock.instances = []
  RecorderMock.isTypeSupported.mockReset().mockImplementation((type) => type === "audio/webm;codecs=opus")
  stopTrack.mockReset(); submit.mockReset()
  getUserMedia.mockReset().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] })
  vi.stubGlobal("MediaRecorder", RecorderMock)
  // Match Node's Request/Response streaming implementation when MSW parses multipart bodies.
  const { Blob: StreamingBlob, File: StreamingFile } = await vi.importActual<{ Blob: typeof Blob; File: typeof File }>("node:buffer")
  const form = await new Response("", { headers: { "Content-Type": "application/x-www-form-urlencoded" } }).formData()
  vi.stubGlobal("Blob", StreamingBlob)
  vi.stubGlobal("File", StreamingFile)
  vi.stubGlobal("FormData", form.constructor)
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } })
  mockStatus({ ...ON, model_classes: { stt: { enabled: true, reason: null } } })
  setCsrfToken("synthetic-csrf")
  server.use(http.post("*/api/v1/voice/transcriptions", ({ request }) => { requests.push(request); return HttpResponse.json(response) }))
})
afterEach(() => {
  vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken(null)
})

describe("staff dictation", () => {
  it("toggles mono recording, uploads metadata + CSRF + key, inserts at cursor, and never sends", async () => {
    const bodies: FormData[] = []
    server.use(http.post("*/api/v1/voice/transcriptions", async ({ request }) => {
      requests.push(request); bodies.push(await request.formData()); return HttpResponse.json(response)
    }))
    mount({ context: "inbox_reply", entity_ids: [THREAD] })
    field().focus(); field().setSelectionRange(7, 7)
    await start()
    expect(getUserMedia).toHaveBeenCalledWith({ audio: { channelCount: 1 }, video: false })
    expect(screen.getByRole("button", { name: "Stop dictation" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByText("Recording", { selector: '[role="status"]' })).toBeInTheDocument()
    expect(screen.getByText("Recording 0:00")).toBeVisible()
    await finish()
    expect(field()).toHaveValue("Before Hello there.after")
    expect(screen.getByTestId("value")).toHaveTextContent("Before Hello there.after")
    expect(field().selectionStart).toBe(19)
    expect(field()).toHaveFocus(); expect(submit).not.toHaveBeenCalled()
    expect(bodies[0]?.get("context")).toBe("inbox_reply")
    expect(bodies[0]?.getAll("entity_ids")).toEqual([THREAD])
    expect((bodies[0]!.get("audio") as File).name).toBe("dictation.webm")
    expect(requests[0]?.headers.get("Idempotency-Key")).toMatch(/^[\da-f-]{36}$/)
    expect(requests[0]?.headers.get("X-CSRF-Token")).toBe("synthetic-csrf")
    expect(stopTrack).toHaveBeenCalled(); expect(RecorderMock.instances[0]?.state).toBe("inactive")
  })
  it("replaces selection and lets the user inspect and substitute raw speech without sending", async () => {
    mount({ context: "knowledge" }); field().setSelectionRange(0, 6)
    await start(); await finish()
    expect(field()).toHaveValue("Hello there. after")
    expect(screen.queryByText(response.raw_text)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Show what I said" }))
    expect(screen.getByText(response.raw_text)).toBeVisible()
    await userEvent.click(screen.getByRole("button", { name: "Use this instead" }))
    expect(field()).toHaveValue("um hello there after")
    expect(screen.getByText("Showing what you said, not cleaned up")).toBeVisible()
    expect(submit).not.toHaveBeenCalled()
  })
  it("shows the unpolished note returned by the backend", async () => {
    server.use(http.post("*/api/v1/voice/transcriptions", () => HttpResponse.json({ ...response, text: response.raw_text, polished: false })))
    mount(); await start(); await finish()
    expect(screen.getByText("Showing what you said, not cleaned up")).toBeVisible()
  })
  it("holds the pointer to record and releases to stop", async () => {
    mount({ mode: "hold" }); const button = await startButton()
    fireEvent.pointerDown(button, { button: 0, pointerId: 1 })
    await screen.findByRole("button", { name: "Stop dictation" })
    fireEvent.pointerUp(button, { pointerId: 1 }); await screen.findByText("Inserted")
    expect(stopTrack).toHaveBeenCalled(); expect(requests).toHaveLength(1)
  })
  it.each([" ", "Enter"])("holds %s on the button to record with only the keyboard", async (key) => {
    mount({ mode: "hold" }); const button = await startButton(); button.focus()
    fireEvent.keyDown(button, { key }); await screen.findByRole("button", { name: "Stop dictation" })
    fireEvent.keyUp(button, { key }); await screen.findByText("Inserted")
    expect(submit).not.toHaveBeenCalled()
  })
  it("Alt+Space holds from the focused field, ignores repeats and preserves spaces / screen-reader modifiers", async () => {
    mount(); const button = await startButton()
    expect(button).toHaveAttribute("title", expect.stringContaining("Alt+Space"))
    field().focus(); field().setSelectionRange(12, 12); await userEvent.keyboard(" "); expect(field()).toHaveValue("Before after ")
    fireEvent.keyDown(field(), { key: " ", code: "Space", altKey: true, ctrlKey: true })
    expect(getUserMedia).not.toHaveBeenCalled()
    fireEvent.keyDown(field(), { key: " ", code: "Space", altKey: true }); await screen.findByRole("button", { name: "Stop dictation" })
    fireEvent.keyDown(field(), { key: " ", code: "Space", altKey: true, repeat: true }); expect(getUserMedia).toHaveBeenCalledTimes(1)
    fireEvent.keyUp(field(), { key: " ", code: "Space", altKey: false }); await screen.findByText("Inserted")
    expect(requests).toHaveLength(1); expect(submit).not.toHaveBeenCalled()
  })
  it("auto-stops at 120 seconds and explains the limit", async () => {
    mount(); const button = await startButton(); vi.useFakeTimers()
    await act(async () => { fireEvent.click(button) })
    await act(() => vi.advanceTimersByTimeAsync(61000)); expect(screen.getByText("Recording 1:01")).toBeVisible()
    await act(() => vi.advanceTimersByTimeAsync(59000)); vi.useRealTimers()
    expect(await screen.findByText("Stopped at 2:00, the recording limit.")).toBeVisible()
    await screen.findByText("Inserted"); expect(stopTrack).toHaveBeenCalled(); expect(requests).toHaveLength(1)
  })
  it("Escape cancels without uploading and stops the tracks", async () => {
    mount(); await start(); fireEvent.keyDown(document, { key: "Escape" })
    expect(field()).toHaveValue("Before after"); expect(field()).toHaveFocus(); expect(stopTrack).toHaveBeenCalled()
    expect(requests).toHaveLength(0); expect(screen.queryByRole("button", { name: "Stop dictation" })).not.toBeInTheDocument()
  })
  it("Cancel discards the recording without uploading", async () => {
    mount(); await start(); await userEvent.click(screen.getByRole("button", { name: "Cancel (Esc)" }))
    expect(stopTrack).toHaveBeenCalled(); expect(requests).toHaveLength(0)
  })
  it("disposes a microphone granted after Escape while consent was pending", async () => {
    let resolve!: (stream: unknown) => void
    getUserMedia.mockImplementation(() => new Promise((r) => { resolve = r }))
    mount(); fireEvent.click(await startButton())
    expect(await screen.findByText("Waiting for microphone permission…")).toBeVisible()
    fireEvent.keyDown(document, { key: "Escape" })
    await act(async () => resolve({ getTracks: () => [{ stop: stopTrack }] }))
    expect(stopTrack).toHaveBeenCalled(); expect(RecorderMock.instances).toHaveLength(0); expect(requests).toHaveLength(0)
  })
  it.each([
    ["NotAllowedError", "Microphone permission was denied. You can type your text instead."],
    ["NotFoundError", "No microphone was found. You can type your text instead."],
    ["NotReadableError", "Could not record from your microphone. Check it or type your text instead."],
  ])("handles %s and focuses text input", async (name, message) => {
    getUserMedia.mockRejectedValue(new DOMException("Synthetic failure", name)); mount(); fireEvent.click(await startButton())
    expect(await screen.findByRole("alert")).toHaveTextContent(message); expect(field()).toHaveFocus()
    await userEvent.type(field(), " typed"); expect(field()).toHaveValue("Before after typed"); expect(requests).toHaveLength(0)
  })
  it("hides the microphone without permission", () => {
    mount({}, []); expect(screen.queryByRole("button", { name: "Start dictation" })).not.toBeInTheDocument()
    field().focus(); fireEvent.keyDown(field(), { code: "Space", altKey: true }); expect(getUserMedia).not.toHaveBeenCalled()
  })
  it.each(["recorder", "getUserMedia", "codec"])("hides the microphone without browser %s support", (missing) => {
    if (missing === "recorder") vi.stubGlobal("MediaRecorder", undefined)
    if (missing === "getUserMedia") vi.stubGlobal("navigator", { mediaDevices: {} })
    if (missing === "codec") RecorderMock.isTypeSupported.mockReturnValue(false)
    mount(); expect(screen.queryByRole("button", { name: "Start dictation" })).not.toBeInTheDocument()
  })
  it("hides the microphone when STT status reports unavailable", async () => {
    mockStatus({ ...ON, model_classes: { stt: { enabled: false, reason: "local-only policy without local speech-to-text" } } })
    const { client } = mount(); await waitFor(() => expect(client.getQueryData(["ai", "status"])).toBeDefined())
    expect(screen.queryByRole("button", { name: "Start dictation" })).not.toBeInTheDocument()
  })
  it("stops the microphone on unmount and never uploads", async () => {
    const view = mount(); await start(); view.unmount(); expect(stopTrack).toHaveBeenCalled(); expect(requests).toHaveLength(0)
  })
  it.each([
    ["voice.audio_too_long", 422, "That recording is too long."], ["voice.audio_too_large", 413, "That recording is too large."],
    ["rate_limit.exceeded", 429, "You have used dictation too often."], ["voice.replay_unavailable", 409, "Please dictate again."],
    ["voice.operation_in_progress", 409, "Still transcribing, try again in a moment."], ["voice.invalid_audio", 422, "That recording could not be read."],
    ["identity.forbidden", 403, "You no longer have permission to dictate."], ["voice.provider_error", 502, "Could not transcribe that recording."],
  ])("explains backend error %s and discards audio", async (code, status, text) => {
    fail(code, status); mount(); await start(); fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(text)
    expect(screen.queryByRole("button", { name: "Retry once" })).not.toBeInTheDocument()
    expect(field()).toHaveValue("Before after"); expect(stopTrack).toHaveBeenCalled()
  })
  it("shows the unavailable reason and disables the microphone", async () => {
    fail("voice.unavailable", 503, "local-only policy without local speech-to-text"); mount(); await start()
    fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("local-only policy without local speech-to-text")
    expect(screen.getByRole("button", { name: "Start dictation" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Start dictation" })).toHaveAttribute("title", expect.stringContaining("local-only policy"))
  })
  it("retains audio for one network retry with the same key and then discards it", async () => {
    const keys: (string | null)[] = []
    server.use(http.post("*/api/v1/voice/transcriptions", ({ request }) => { keys.push(request.headers.get("Idempotency-Key")); return HttpResponse.error() }))
    mount(); await start(); fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server")
    await userEvent.click(screen.getByRole("button", { name: "Retry once" })); await waitFor(() => expect(keys).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry once" })).not.toBeInTheDocument())
    expect(keys[0]).toBe(keys[1]); expect(getUserMedia).toHaveBeenCalledTimes(1); expect(field()).toHaveValue("Before after")
    expect(screen.getByRole("alert")).toHaveTextContent("The recording was discarded")
    await start(); fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    await screen.findByRole("button", { name: "Retry once" })
    fireEvent.click(screen.getByRole("button", { name: "Cancel (Esc)" }))
    expect(screen.queryByRole("button", { name: "Retry once" })).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(keys).toHaveLength(3); expect(keys[2]).not.toBe(keys[0])
  })
  it("network retry succeeds without recording again", async () => {
    let count = 0
    server.use(http.post("*/api/v1/voice/transcriptions", () => ++count === 1 ? HttpResponse.error() : HttpResponse.json(response)))
    mount(); await start(); fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    await userEvent.click(await screen.findByRole("button", { name: "Retry once" })); await screen.findByText("Inserted")
    expect(getUserMedia).toHaveBeenCalledTimes(1)
  })
  it("never uses browser persistence or object URLs", async () => {
    const storage = (["setItem", "removeItem", "clear", "key"] as const).map((method) => vi.spyOn(Storage.prototype, method))
    const reads = vi.spyOn(Storage.prototype, "getItem")
    const indexed = vi.fn(() => { throw Error("Audio must not use IndexedDB") })
    const createURL = vi.fn(); vi.stubGlobal("indexedDB", { open: indexed })
    vi.stubGlobal("URL", class extends URL { static override createObjectURL = createURL })
    mount(); await start(); await finish()
    for (const method of storage) expect(method).not.toHaveBeenCalled()
    // MSW's logger checks its debugLevel flag; dictation must not read any application storage key.
    for (const [key] of reads.mock.calls) expect(key).toBe("debugLevel")
    expect(indexed).not.toHaveBeenCalled(); expect(createURL).not.toHaveBeenCalled()
  })
  it("uses Ogg then Safari MP4 when earlier MIME types are unsupported", () => {
    RecorderMock.isTypeSupported.mockImplementation((type) => type === "audio/ogg;codecs=opus" || type === "audio/mp4")
    expect(dictationMimeType()).toBe("audio/ogg;codecs=opus")
    RecorderMock.isTypeSupported.mockImplementation((type) => type === "audio/mp4"); expect(dictationMimeType()).toBe("audio/mp4")
  })
  it("does not overwrite edits made while transcribing", async () => {
    let resolve!: () => void
    server.use(http.post("*/api/v1/voice/transcriptions", async () => { await new Promise<void>((r) => { resolve = r }); return HttpResponse.json(response) }))
    mount(); field().setSelectionRange(0, 6); await start(); fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    await waitFor(() => expect(resolve).toBeDefined())
    fireEvent.change(field(), { target: { value: "Edited: " } }); field().setSelectionRange(8, 8)
    await act(async () => resolve()); await screen.findByText("Inserted"); expect(field()).toHaveValue("Edited: Hello there.")
  })
  it("respects the field length without silently truncating a transcript", async () => {
    mount({}, ["ai.assistant.use"], 15); await start(); fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("The transcript will not fit")
    expect(screen.queryByText("Transcribing…")).not.toBeInTheDocument()
    expect(field()).toHaveValue("Before after"); expect(submit).not.toHaveBeenCalled()
  })

  it("lets the user choose hold-to-talk and cancels a lost pointer", async () => {
    mount(); const button = await startButton()
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Dictation mode" }), "hold")
    fireEvent.pointerDown(button, { button: 0, pointerId: 1 })
    await screen.findByRole("button", { name: "Stop dictation" })
    fireEvent.pointerCancel(button, { pointerId: 1 })
    expect(stopTrack).toHaveBeenCalled(); expect(requests).toHaveLength(0)
  })

  it("shows transcribing and ignores an aborted response without inserting", async () => {
    let resolve!: () => void
    server.use(http.post("*/api/v1/voice/transcriptions", async () => {
      await new Promise<void>((r) => { resolve = r }); return HttpResponse.json(response)
    }))
    mount(); await start(); fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    await waitFor(() => expect(resolve).toBeDefined())
    expect(screen.getByText("Transcribing…", { selector: '[role="status"]' })).toBeInTheDocument()
    expect(stopTrack).toHaveBeenCalled()
    fireEvent.keyDown(document, { key: "Escape" })
    await act(async () => resolve())
    expect(field()).toHaveValue("Before after"); expect(submit).not.toHaveBeenCalled()
  })

  it("discards oversize audio locally and never uploads it", async () => {
    mount(); await start()
    act(() => RecorderMock.instances[0]!.ondataavailable?.({ data: new Blob([new Uint8Array(4 * 1024 * 1024 + 1)]) }))
    expect(screen.getByRole("alert")).toHaveTextContent("That recording is too large")
    expect(stopTrack).toHaveBeenCalled(); expect(requests).toHaveLength(0)
  })

  it("handles a recorder error with stopped tracks and a text fallback", async () => {
    mount(); await start()
    act(() => RecorderMock.instances[0]!.onerror?.())
    expect(screen.getByRole("alert")).toHaveTextContent("Could not record from your microphone")
    expect(field()).toHaveFocus(); expect(stopTrack).toHaveBeenCalled(); expect(requests).toHaveLength(0)
  })

  it("uses STT's availability independently of the text model", async () => {
    mockStatus({ ...ON, enabled: false, model_classes: { stt: { enabled: true, reason: null } } })
    mount(); field().setSelectionRange(12, 12); await start(); await finish()
    expect(field()).toHaveValue("Before afterHello there.")
  })

  it("discards a retry when the server says its cached transcript is unavailable", async () => {
    let count = 0
    server.use(http.post("*/api/v1/voice/transcriptions", () => ++count === 1 ? HttpResponse.error() : HttpResponse.json({ code: "voice.replay_unavailable", status: 409 }, { status: 409 })))
    mount(); await start(); fireEvent.click(screen.getByRole("button", { name: "Stop dictation" }))
    await userEvent.click(await screen.findByRole("button", { name: "Retry once" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Please dictate again")
    expect(screen.queryByRole("button", { name: "Retry once" })).not.toBeInTheDocument()
  })

  it("cancels recording when the tab becomes hidden", async () => {
    mount(); await start()
    vi.spyOn(document, "hidden", "get").mockReturnValue(true)
    fireEvent(document, new Event("visibilitychange"))
    expect(stopTrack).toHaveBeenCalled(); expect(requests).toHaveLength(0)
  })
})
