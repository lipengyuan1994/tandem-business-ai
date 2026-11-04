// Plain fetch for AI endpoints that are not in the generated OpenAPI schema yet (see types.ts). Same rules as
// `api/client.ts`: same-origin cookies, CSRF header on unsafe methods, problem+json turned into ApiProblem.
// Once an endpoint is generated, use `api.GET/POST` in api.ts and delete the call here.
import { getCsrfToken } from "@/api/client"
import { toApiProblem } from "@/lib/problem"

const BASE = "/api/v1/ai"

export function aiHeaders(method: string, hasBody: boolean): Headers {
  const headers = new Headers({ Accept: "application/json" })
  if (hasBody) headers.set("Content-Type", "application/json")
  const csrf = getCsrfToken()
  if (csrf && method !== "GET") headers.set("X-CSRF-Token", csrf)
  return headers
}

export function aiUrl(path: string): string {
  return `${globalThis.location?.origin ?? ""}${BASE}${path}`
}

export async function problemFrom(response: Response) {
  const body: unknown = await response.json().catch(() => null)
  return toApiProblem(body, response.status)
}

export async function aiRequest<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const response = await globalThis.fetch(aiUrl(path), {
    method,
    credentials: "same-origin",
    headers: aiHeaders(method, body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw await problemFrom(response)
  return (await response.json()) as T
}
