import { ApiProblem } from "@/lib/problem"

export function fieldInvalid(error: unknown, field: string): boolean {
  if (!(error instanceof ApiProblem) || error.status !== 422) return false
  return Object.keys(error.fieldErrors()).some((key) => key === field || key === `body.${field}` || key.startsWith(`${field}.`) || key.startsWith(`body.${field}.`))
}
