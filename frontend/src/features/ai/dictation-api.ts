import type { TFunction } from "i18next"
import { api, unwrap } from "@/api/client"
import type { components, operations } from "@/api/schema"
import { ApiProblem } from "@/lib/problem"

type Upload = operations["transcriptions_api_v1_voice_transcriptions_post"]["requestBody"]["content"]["multipart/form-data"]
export type DictationOptions = { context?: Upload["context"]; entity_ids?: string[]; mode?: "toggle" | "hold" }
export type DictationResult = components["schemas"]["DictationOut"]

export async function transcribe(audio: Blob, key: string, options: DictationOptions, signal: AbortSignal): Promise<DictationResult> {
  const extension = audio.type.startsWith("audio/mp4") ? "mp4" : audio.type.startsWith("audio/ogg") ? "ogg" : "webm"
  return unwrap(await api.POST("/api/v1/voice/transcriptions", {
    params: { header: { "idempotency-key": key } },
    signal,
    body: { audio: "", context: options.context, entity_ids: options.entity_ids },
    bodySerializer: () => {
      const form = new FormData()
      form.append("audio", audio, `dictation.${extension}`)
      form.append("context", options.context ?? "other")
      for (const id of options.entity_ids ?? []) form.append("entity_ids", id)
      return form
    },
  }))
}

export function dictationError(t: TFunction, error: unknown): string {
  if (!(error instanceof ApiProblem)) return t("dictation.errors.network")
  if (error.code === "voice.unavailable") {
    const reason = error.problem.reason ?? error.problem.detail ?? error.problem.title
    return t("dictation.errors.unavailable", { reason: typeof reason === "string" ? reason : t("dictation.unknown") })
  }
  if (error.status === 413) return t("dictation.errors.tooLarge")
  if (error.status === 429) return t("dictation.errors.rateLimited")
  const codes: Record<string, string> = {
    "voice.replay_unavailable": "replay",
    "voice.operation_in_progress": "inProgress",
    "voice.too_long": "tooLong",
    "voice.audio_too_long": "tooLong",
    "voice.audio_too_large": "tooLarge",
  }
  return t(`dictation.errors.${codes[error.code] ?? (error.status === 422 ? "invalid" : error.status === 403 ? "forbidden" : "generic")}`)
}
