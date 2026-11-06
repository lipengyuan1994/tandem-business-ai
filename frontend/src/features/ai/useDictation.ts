import { useCallback, useEffect, useRef, useState, type RefObject } from "react"
import { useTranslation } from "react-i18next"
import { ApiProblem } from "@/lib/problem"
import { dictationError, transcribe, type DictationOptions, type DictationResult } from "./dictation-api"

const TYPES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"]
const MAX_BYTES = 4 * 1024 * 1024
export function dictationMimeType() {
  if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) return undefined
  return TYPES.find((type) => MediaRecorder.isTypeSupported(type))
}
type Phase = "idle" | "requesting" | "recording" | "transcribing"
type Range = { value: string; start: number; end: number }
type Pending = { audio: Blob; key: string; options: DictationOptions; range: Range }
type Recording = { recorder?: MediaRecorder; stream?: MediaStream; chunks: Blob[]; bytes: number; timer?: ReturnType<typeof setInterval>; deadline?: ReturnType<typeof setTimeout>; range: Range; options: DictationOptions }

/** Audio is bounded, transient memory only. No object URLs, browser speech services or storage. */
export function useDictation(target: RefObject<HTMLTextAreaElement | null>, options: DictationOptions) {
  const { t } = useTranslation()
  const [phase, setPhase] = useState<Phase>("idle")
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [limit, setLimit] = useState(false)
  const [announcement, setAnnouncement] = useState("")
  const [result, setResult] = useState<DictationResult | null>(null)
  const [canRetry, setCanRetry] = useState(false)
  const active = useRef<Recording | null>(null)
  const pending = useRef<Pending | null>(null)
  const controller = useRef<AbortController | null>(null)
  const mounted = useRef(true)
  const held = useRef(false)
  const inserted = useRef<Range | null>(null)
  const phaseRef = useRef<Phase>("idle")
  const transition = (value: Phase) => { phaseRef.current = value; setPhase(value) }

  const release = (session: Recording) => {
    clearInterval(session.timer)
    clearTimeout(session.deadline)
    session.stream?.getTracks().forEach((track) => track.stop())
  }
  const cancel = useCallback(() => {
    const session = active.current
    active.current = null
    if (session) {
      release(session)
      if (session.recorder?.state !== "inactive") session.recorder?.stop()
      session.chunks = []
    }
    controller.current?.abort()
    controller.current = null
    pending.current = null
    held.current = false
    phaseRef.current = "idle"
    if (mounted.current) {
      setPhase("idle")
      setCanRetry(false)
      setAnnouncement("")
      setError(null)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; cancel() }
  }, [cancel])

  const insert = (text: string, range?: Range) => {
    const field = target.current
    if (!field || field.matches(":disabled") || field.readOnly) return false
    const same = range?.value === field.value
    const start = same ? range.start : field.selectionStart
    const end = same ? range.end : field.selectionEnd
    const value = field.value.slice(0, start) + text + field.value.slice(end)
    if (field.maxLength >= 0 && value.length > field.maxLength) {
      setError(t("dictation.errors.fieldLimit"))
      field.focus()
      return false
    }
    // The native setter bypasses React's value tracker; the input event updates controlled forms.
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, value)
    field.dispatchEvent(new Event("input", { bubbles: true }))
    field.focus()
    field.setSelectionRange(start + text.length, start + text.length)
    setError(null)
    inserted.current = { value, start, end: start + text.length }
    setAnnouncement(t("dictation.inserted"))
    return true
  }

  const upload = async (item: Pending, retry: boolean) => {
    const abort = new AbortController()
    controller.current = abort
    transition("transcribing")
    setAnnouncement(t("dictation.transcribing"))
    setCanRetry(false)
    setError(null)
    try {
      const data = await transcribe(item.audio, item.key, item.options, abort.signal)
      if (!mounted.current || abort.signal.aborted) return
      setResult(data)
      if (!insert(data.text, item.range)) setAnnouncement("")
      pending.current = null
    } catch (cause) {
      if (!mounted.current || abort.signal.aborted) return
      setError(dictationError(t, cause))
      const network = cause instanceof TypeError
      const retryable = network && !retry
      if (network && retry) setError(t("dictation.errors.networkFinal"))
      setCanRetry(retryable)
      pending.current = retryable ? item : null
      if (cause instanceof ApiProblem && cause.code === "voice.unavailable") setUnavailable(true)
      target.current?.focus()
      setAnnouncement("")
    } finally {
      if (controller.current === abort) {
        controller.current = null
        if (mounted.current) transition("idle")
      }
    }
  }

  const stop = () => {
    held.current = false
    const session = active.current
    if (!session) return
    // Releasing a held shortcut while the consent dialog is open cancels that request.
    if (!session.recorder) { cancel(); return }
    release(session)
    if (session.recorder.state !== "inactive") session.recorder.stop()
  }

  const start = async (hold = false) => {
    const field = target.current
    const mimeType = dictationMimeType()
    if (phaseRef.current !== "idle" || !field || field.matches(":disabled") || field.readOnly || !mimeType || unavailable) return
    pending.current = null
    setCanRetry(false)
    setError(null)
    setResult(null)
    inserted.current = null
    setLimit(false)
    setElapsed(0)
    held.current = hold
    const session: Recording = { chunks: [], bytes: 0, range: { value: field.value, start: field.selectionStart, end: field.selectionEnd }, options: { ...options, entity_ids: options.entity_ids?.slice() } }
    active.current = session
    transition("requesting")
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 }, video: false })
      session.stream = stream
      if (active.current !== session || !mounted.current) { release(session); return }
      const recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 24000 })
      session.recorder = recorder
      recorder.ondataavailable = (event) => {
        if (active.current !== session) return
        session.bytes += event.data.size
        if (session.bytes > MAX_BYTES) { cancel(); setError(t("dictation.errors.tooLarge")); return }
        session.chunks.push(event.data)
      }
      recorder.onerror = () => { if (active.current === session) { cancel(); setError(t("dictation.errors.microphone")); target.current?.focus() } }
      recorder.onstop = () => {
        if (active.current !== session) return
        active.current = null
        release(session)
        const audio = new Blob(session.chunks, { type: recorder.mimeType || mimeType })
        session.chunks = []
        if (!audio.size) { transition("idle"); setError(t("dictation.errors.invalid")); return }
        void upload({ audio, key: crypto.randomUUID(), options: session.options, range: session.range }, false)
      }
      recorder.start(1000)
      transition("recording")
      setAnnouncement(t("dictation.recording"))
      const started = Date.now()
      session.timer = setInterval(() => setElapsed(Math.min(120, Math.floor((Date.now() - started) / 1000))), 1000)
      session.deadline = setTimeout(() => { setLimit(true); setElapsed(120); stop() }, 120000)
    } catch (cause) {
      release(session)
      if (active.current !== session || !mounted.current) return
      active.current = null
      held.current = false
      transition("idle")
      const name = cause instanceof Error || cause instanceof DOMException ? cause.name : ""
      setError(t(`dictation.errors.${name === "NotAllowedError" || name === "SecurityError" ? "denied" : name === "NotFoundError" || name === "DevicesNotFoundError" ? "noMicrophone" : "microphone"}`))
      field.focus()
    }
  }

  // Capture Escape before modal handlers close the editor. Plain Space and assistive modifiers are untouched.
  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      if (event.key === "Escape" && (phaseRef.current !== "idle" || pending.current)) {
        event.preventDefault(); event.stopPropagation(); cancel(); target.current?.focus()
      } else if (event.code === "Space" && event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.isComposing && event.target === target.current && !event.repeat) {
        event.preventDefault()
        if (phaseRef.current === "idle") void start(true)
      } else if (event.code === "Space" && held.current && event.altKey && !event.ctrlKey && !event.metaKey) event.preventDefault()
    }
    const up = (event: KeyboardEvent) => { if (held.current && (event.code === "Space" || event.key === "Alt")) { event.preventDefault(); stop() } }
    const blur = () => { if (held.current) stop() }
    const hidden = () => { if (document.hidden && active.current) cancel() }
    document.addEventListener("keydown", down, true)
    document.addEventListener("keyup", up, true)
    window.addEventListener("blur", blur)
    document.addEventListener("visibilitychange", hidden)
    return () => { document.removeEventListener("keydown", down, true); document.removeEventListener("keyup", up, true); window.removeEventListener("blur", blur); document.removeEventListener("visibilitychange", hidden) }
  })

  return { phase, elapsed, error, unavailable, limit, announcement, result, canRetry, start, stop, cancel,
    retry: () => { const item = pending.current; if (item) { pending.current = null; void upload(item, true) } },
    useRaw: () => { if (result && insert(result.raw_text, inserted.current ?? undefined)) setResult({ ...result, text: result.raw_text, polished: false }) },
  }
}
