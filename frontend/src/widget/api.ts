// Visitor API client: same generated types as the staff app, no session or CSRF (the visitor cookie is path-scoped).
import createClient from "openapi-fetch"
import type { components, paths } from "@/api/schema"

export type Profile = components["schemas"]["PublicProfileOut"]
export type PublicService = components["schemas"]["PublicServiceOut"]
export type PublicThread = components["schemas"]["PublicThreadOut"]

export const client = createClient<paths>({ baseUrl: globalThis.location?.origin ?? "", credentials: "same-origin", fetch: (r) => globalThis.fetch(r) })

export class WidgetError extends Error {
  readonly status: number
  readonly code: string
  readonly fields: Record<string, string>

  constructor(status: number, code: string, fields: Record<string, string> = {}) {
    super(code)
    this.status = status
    this.code = code
    this.fields = fields
  }
}

export async function call<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const r = await p
  if (!r.response.ok || r.data === undefined) {
    const code = typeof r.error === "object" && r.error && "code" in r.error ? String((r.error as { code: unknown }).code) : "unknown"
    const fields: Record<string, string> = {}
    if (typeof r.error === "object" && r.error && "errors" in r.error && Array.isArray(r.error.errors)) {
      for (const field of r.error.errors) {
        if (typeof field === "object" && field && "field" in field && typeof field.field === "string") fields[field.field] = "invalid"
      }
    }
    throw new WidgetError(r.response.status, code, fields)
  }
  return r.data
}

/** "/p/example-cycle-works/chat" → { slug, page }. */
export function parseLocation(pathname: string): { slug: string; page: "chat" | "request" } | null {
  const m = /^\/p\/([a-z0-9-]+)\/(chat|request)\/?$/.exec(pathname)
  return m ? { slug: m[1]!, page: m[2] as "chat" | "request" } : null
}

/** Capability tokens are URL-safe base64 components separated by a dot. */
export function parseQuoteLocation(pathname: string): string | null {
  return /^\/q\/([A-Za-z0-9_.-]{1,600})\/?$/.exec(pathname)?.[1] ?? null
}


const embedMemory = new Map<string, string>()
function readEmbedToken(slug: string) {
  try { return sessionStorage.getItem(`smb_embed_${slug}`) ?? embedMemory.get(slug) }
  catch { return embedMemory.get(slug) }
}
client.use({
  async onRequest({ request }) {
    const url = new URL(request.url)
    const embed = /^\/embed\/([a-z0-9-]+)\/chat\/?$/.exec(location.pathname)
    if (url.origin !== location.origin) return request
    if (request.method !== "GET" && request.method !== "HEAD") request.headers.set("Origin", location.origin)
    if (embed && url.pathname.startsWith(`/api/v1/public/${embed[1]}/chat`)) {
      const slug = embed[1]!
      url.pathname = url.pathname.replace(`/${slug}/chat`, `/${slug}/embed/chat`)
      const headers = new Headers(request.headers)
      const token = readEmbedToken(slug)
      if (token) headers.set("X-Embed-Token", token)
      return new Request(url, { method: request.method, headers, credentials: "omit", signal: request.signal,
        body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.clone().arrayBuffer() })
    }
    return request
  },
  onResponse({ request, response }) {
    const slug = /\/public\/([a-z0-9-]+)\/embed\/chat$/.exec(new URL(request.url).pathname)?.[1]
    const token = response.headers.get("X-Embed-Token")
    if (slug && token && new URL(request.url).origin === location.origin) {
      embedMemory.set(slug, token)
      try { sessionStorage.setItem(`smb_embed_${slug}`, token) } catch { /* memory fallback */ }
    }
    return response
  },
})
