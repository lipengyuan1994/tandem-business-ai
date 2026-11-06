import { Mic, Square } from "lucide-react"
import { useId, useState, type RefObject } from "react"
import { useTranslation } from "react-i18next"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { useAiStatus } from "./api"
import type { DictationOptions } from "./dictation-api"
import { dictationMimeType, useDictation } from "./useDictation"

type Props = { target: RefObject<HTMLTextAreaElement | null>; options: DictationOptions; disabled?: boolean }

export function DictationButton(props: Props) {
  return <PermissionGate permission="ai.assistant.use"><AvailableDictation {...props} /></PermissionGate>
}

function AvailableDictation(props: Props) {
  const status = useAiStatus()
  const stt = status.data?.model_classes?.stt
  if (!dictationMimeType() || !status.data || !(stt?.enabled ?? status.data.enabled) || props.disabled) return null
  return <Controls {...props} />
}

function Controls({ target, options }: Props) {
  const { t } = useTranslation()
  const id = useId()
  const [mode, setMode] = useState(options.mode ?? "toggle")
  const [showRaw, setShowRaw] = useState(false)
  const voice = useDictation(target, options)
  const recording = voice.phase === "recording"
  const busy = voice.phase !== "idle"
  const tooltip = voice.unavailable ? voice.error ?? "" : t("dictation.tooltip")
  const stop = () => { if (mode === "hold") voice.stop() }
  return <div className="mt-1 space-y-1 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <Button type="button" size="sm" variant="outline" aria-pressed={recording} aria-label={t(recording ? "dictation.stop" : "dictation.start")}
        aria-describedby={id} aria-keyshortcuts="Alt+Space" title={tooltip}
        disabled={voice.unavailable || voice.phase === "transcribing"}
        className="touch-none"
        onClick={() => { if (mode === "toggle") { setShowRaw(false); if (voice.phase === "requesting") voice.cancel(); else if (recording) voice.stop(); else void voice.start() } }}
        onPointerDown={(event) => {
          if (mode !== "hold" || event.button !== 0) return
          event.preventDefault(); event.currentTarget.setPointerCapture?.(event.pointerId); setShowRaw(false); void voice.start(true)
        }}
        onPointerUp={stop} onPointerCancel={() => { if (mode === "hold") voice.cancel() }} onLostPointerCapture={stop}
        onKeyDown={(event) => {
          if (mode === "hold" && (event.key === " " || event.key === "Enter")) {
            event.preventDefault(); if (!event.repeat) { setShowRaw(false); void voice.start(true) }
          }
        }}
        onKeyUp={(event) => { if (mode === "hold" && (event.key === " " || event.key === "Enter")) { event.preventDefault(); voice.stop() } }}
        onBlur={stop}>
        {recording ? <Square aria-hidden="true" className="size-4" /> : <Mic aria-hidden="true" className="size-4" />}
        {t(recording ? "dictation.stop" : "dictation.start")}
      </Button>
      <label className="flex items-center gap-1">{t("dictation.mode")}
        <select className="min-h-9 rounded-md border bg-background px-1" value={mode} disabled={busy} onChange={(event) => setMode(event.target.value as "toggle" | "hold")}>
          <option value="toggle">{t("dictation.toggle")}</option><option value="hold">{t("dictation.hold")}</option>
        </select>
      </label>
      {recording ? <span className="inline-flex items-center gap-1 tabular-nums"><span aria-hidden="true" className="size-2 rounded-full bg-destructive motion-safe:animate-pulse" />{t("dictation.recording")} {Math.floor(voice.elapsed / 60)}:{String(voice.elapsed % 60).padStart(2, "0")}</span> : null}
      {voice.phase === "requesting" ? <span>{t("dictation.requesting")}</span> : null}
      {voice.phase === "transcribing" ? <span>{t("dictation.transcribing")}</span> : null}
      {busy || voice.canRetry ? <Button type="button" size="sm" variant="ghost" onClick={() => { voice.cancel(); target.current?.focus() }}>{t("dictation.cancel")}</Button> : null}
      {voice.canRetry ? <Button type="button" size="sm" variant="outline" onClick={voice.retry}>{t("dictation.retry")}</Button> : null}
    </div>
    <span id={id} className="sr-only">{tooltip}</span>
    <span role="status" aria-live="polite" className="sr-only">{voice.announcement}</span>
    {voice.limit ? <p className="text-muted-foreground">{t("dictation.limit")}</p> : null}
    {voice.error ? <p role="alert">{voice.error}</p> : null}
    {voice.result && !voice.result.polished ? <p className="text-muted-foreground">{t("dictation.unpolished")}</p> : null}
    {voice.result ? <>
      <Button type="button" size="sm" variant="ghost" aria-expanded={showRaw} aria-controls={`${id}-raw`} onClick={() => setShowRaw(!showRaw)}>{t("dictation.showRaw")}</Button>
      {showRaw ? <div id={`${id}-raw`} className="space-y-1 rounded-md border p-2"><p className="whitespace-pre-wrap break-words">{voice.result.raw_text}</p><Button type="button" size="sm" variant="outline" onClick={voice.useRaw}>{t("dictation.useRaw")}</Button></div> : null}
    </> : null}
  </div>
}
