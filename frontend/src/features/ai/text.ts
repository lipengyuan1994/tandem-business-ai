// Plain-words text for AI error codes and "AI is off" reasons (all copy is in en.json under ai.*).
import type { TFunction } from "i18next"
import { useAiStatus } from "./api"

/** Text for an `ai.*` error code from the API or from an `error` event. Other codes use the shared `errors.*` copy. */
export function aiErrorText(t: TFunction, code: string | null | undefined): string {
  if (!code) return t("ai.errors.generic")
  if (!code.startsWith("ai.")) return t(`errors.${code}`, { defaultValue: t("ai.errors.generic") })
  return t(`ai.errors.${code.slice(3)}`, { defaultValue: t("ai.errors.generic") })
}

export function disabledText(t: TFunction, reason: string | null | undefined): string {
  const key = (reason ?? "").replace(/^ai\./, "")
  return t(`ai.disabled.${key}`, { defaultValue: t("ai.disabled.unknown") })
}

/** `ready` is false until the status is known. `enabled` is true only when AI can run now. */
export function useAiAvailability() {
  const status = useAiStatus()
  return { status: status.data, ready: !!status.data, enabled: status.data?.enabled === true, error: status.error }
}
