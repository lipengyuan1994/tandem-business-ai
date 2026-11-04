// The only place the frontend talks HTTP. Types come from the generated schema (`make gen-api`); never hand-write them.
import createClient, { type Middleware } from "openapi-fetch"
import type { paths } from "./schema"
import { ApiProblem, toApiProblem } from "@/lib/problem"

let csrfToken: string | null = null

/** Set from /auth/me after login (task P2-08). */
export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

/** For code that cannot use `api` (streamed responses). */
export function getCsrfToken(): string | null {
  return csrfToken
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"])

const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (csrfToken && UNSAFE.has(request.method)) request.headers.set("X-CSRF-Token", csrfToken)
    return request
  },
}

// Same-origin absolute base URL (Node test runtimes reject relative URLs; browsers behave identically).
// `fetch` is looked up per call (not captured at import), so test interceptors and polyfills apply.
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  credentials: "same-origin",
  fetch: (request) => globalThis.fetch(request),
})
api.use(csrfMiddleware)

interface FetchResult<T> {
  data?: T
  error?: unknown
  response: Response
}

/** Return data or throw ApiProblem (409 → conflict dialog, 422 → field errors, others → toast). */
export function unwrap<T>(result: FetchResult<T>): T {
  if (result.error !== undefined || !result.response.ok) {
    throw toApiProblem(result.error, result.response.status)
  }
  return result.data as T
}

export { ApiProblem }
