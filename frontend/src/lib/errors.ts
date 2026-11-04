// Standard handling of API errors in mutations: 409 → caller's conflict dialog, everything else → toast.
import { toast } from "sonner"
import i18n from "@/app/i18n"
import { ApiProblem } from "./problem"

export function describeError(error: unknown): string {
  if (error instanceof ApiProblem) {
    const key = `errors.${error.code}`
    return i18n.exists(key) ? i18n.t(key) : (error.problem.detail ?? error.problem.title)
  }
  return error instanceof Error ? error.message : String(error)
}

export function toastError(error: unknown, onConflict?: () => void): void {
  if (onConflict && error instanceof ApiProblem && error.isConflict && error.code.endsWith("version_conflict")) {
    onConflict()
    return
  }
  toast.error(describeError(error))
}
