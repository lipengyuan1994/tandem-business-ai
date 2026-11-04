// API calls with the signed-in user's session and CSRF token (for setup steps the UI does not offer yet).
import { type APIRequestContext, expect } from "@playwright/test"

export async function csrf(request: APIRequestContext): Promise<string> {
  const me = await request.get("/api/v1/auth/me")
  expect(me.ok(), "signed in").toBe(true)
  return ((await me.json()) as { csrf_token: string }).csrf_token
}

export async function api<T>(request: APIRequestContext, method: "GET" | "POST" | "PATCH" | "PUT", path: string, data?: unknown): Promise<T> {
  const headers: Record<string, string> = method === "GET" ? {} : { "X-CSRF-Token": await csrf(request) }
  const response = await request.fetch(path, { method, data, headers })
  expect(response.ok(), `${method} ${path} → ${response.status()} ${await response.text()}`).toBe(true)
  return (await response.json()) as T
}
