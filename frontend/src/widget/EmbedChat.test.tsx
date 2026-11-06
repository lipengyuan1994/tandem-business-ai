import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, expect, it, vi } from "vitest"
import { server } from "@/test/server"
import { EmbedChat } from "./EmbedChat"
const BASE = "*/api/v1/public/example-cycle-works"
afterEach(() => { history.replaceState(null, "", "/"); sessionStorage.clear(); vi.restoreAllMocks() })

it("uses a header capability in the iframe without a cookie and retains disclosure and hand-over", async () => {
  history.replaceState(null, "", "/embed/example-cycle-works/chat")
  let credentials = ""
  const fetch = globalThis.fetch
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    if (input instanceof Request && input.method === "POST" && input.url.endsWith("/embed/chat")) credentials = input.credentials
    return fetch(input, init)
  })
  let token = ""
  let handed = false
  let sent = false
  const thread = { business_name: "Example Cycle Works", ai_greeting: "You're chatting with Example Cycle Works's AI assistant.", conversation_id: "c1", messages: [] }
  server.use(
    http.get(`${BASE}/profile`, () => HttpResponse.json({ name: "Example Cycle Works", ai_greeting: thread.ai_greeting, ai_policy: "hosted_vultr", avoid_medical_details: false })),
    http.get(`${BASE}/embed-config`, () => HttpResponse.json({ label: "Ask Example", allowed_origins: ["https://shop.example"] })),
    http.get(`${BASE}/embed/chat`, ({ request }) => request.headers.get("X-Embed-Token") === "embed-capability" ? HttpResponse.json(thread) : HttpResponse.json({ code: "conversations.visitor_token_invalid" }, { status: 403 })),
    http.post(`${BASE}/embed/chat`, ({ request }) => { expect(request.url).toContain("/embed/chat"); return HttpResponse.json(thread, { status: 201, headers: { "X-Embed-Token": "embed-capability" } }) }),
    http.post(`${BASE}/embed/chat/messages`, ({ request }) => { token = request.headers.get("X-Embed-Token") ?? ""; sent = true; return HttpResponse.json(thread, { status: 201 }) }),
    http.post(`${BASE}/embed/chat/handoff`, ({ request }) => { handed = request.headers.get("X-Embed-Token") === "embed-capability"; return HttpResponse.json(thread) }),
  )
  render(<EmbedChat slug="example-cycle-works" />)
  await userEvent.type(await screen.findByLabelText("Your name"), "Ben Example")
  await userEvent.type(screen.getByLabelText("Email"), "ben@example.com")
  await userEvent.type(screen.getByLabelText("Message"), "Hello")
  await userEvent.click(screen.getByRole("button", { name: "Send message" }))
  await screen.findByRole("button", { name: "Talk to a person" })
  expect(credentials).toBe("omit")
  expect(screen.getByText(thread.ai_greeting)).toBeInTheDocument()
  await userEvent.type(await screen.findByLabelText("Message"), "Another")
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  await waitFor(() => expect(sent).toBe(true)); expect(token).toBe("embed-capability")
  expect(sessionStorage.getItem("smb_embed_example-cycle-works")).toBe("embed-capability")
  await userEvent.click(screen.getByRole("button", { name: "Talk to a person" }))
  await waitFor(() => expect(handed).toBe(true))
  expect(screen.getByText("Privacy")).toBeInTheDocument()
})

it("continues in memory when iframe sessionStorage is blocked", async () => {
  history.replaceState(null, "", "/embed/example-cycle-works/chat")
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError") })
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError") })
  const thread = { business_name: "Example Cycle Works", ai_greeting: "AI assistant", conversation_id: "c2", messages: [] }
  let sent = false
  server.use(
    http.get(`${BASE}/profile`, () => HttpResponse.json({ name: "Example Cycle Works", ai_greeting: "AI assistant", ai_policy: "hosted_vultr" })),
    http.get(`${BASE}/embed-config`, () => HttpResponse.json({ label: "Chat", allowed_origins: [] })),
    http.get(`${BASE}/embed/chat`, () => HttpResponse.json({ code: "conversations.visitor_token_invalid" }, { status: 403 })),
    http.post(`${BASE}/embed/chat`, () => HttpResponse.json(thread, { status: 201, headers: { "X-Embed-Token": "memory-capability" } })),
    http.post(`${BASE}/embed/chat/messages`, ({ request }) => { sent = request.headers.get("X-Embed-Token") === "memory-capability"; return HttpResponse.json(thread, { status: 201 }) }),
  )
  render(<EmbedChat slug="example-cycle-works" />)
  await userEvent.type(await screen.findByLabelText("Your name"), "Ben Example")
  await userEvent.type(screen.getByLabelText("Email"), "ben@example.com")
  await userEvent.type(screen.getByLabelText("Message"), "Hello")
  await userEvent.click(screen.getByRole("button", { name: "Send message" }))
  await screen.findByRole("button", { name: "Talk to a person" })
  await userEvent.type(screen.getByLabelText("Message"), "Another")
  await userEvent.click(screen.getByRole("button", { name: "Send" }))
  await waitFor(() => expect(sent).toBe(true))
})

it("accepts iframe controls only from an allowed parent and targets that origin for resize/close", async () => {
  history.replaceState(null, "", "/embed/example-cycle-works/chat")
  const post = vi.spyOn(window, "postMessage").mockImplementation(() => {})
  server.use(
    http.get(`${BASE}/profile`, () => HttpResponse.json({ name: "Example Cycle Works", ai_greeting: "AI assistant", ai_policy: "hosted_vultr" })),
    http.get(`${BASE}/embed-config`, () => HttpResponse.json({ label: "Chat", allowed_origins: ["https://shop.example"] })),
    http.get(`${BASE}/embed/chat`, () => HttpResponse.json({ code: "conversations.visitor_token_invalid" }, { status: 403 })),
  )
  render(<EmbedChat slug="example-cycle-works" />)
  const name = await screen.findByLabelText("Your name")
  window.dispatchEvent(new MessageEvent("message", { origin: "https://evil.example", source: window, data: { type: "smb:open" } }))
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
  expect(post).not.toHaveBeenCalled()
  window.dispatchEvent(new MessageEvent("message", { origin: "https://shop.example", source: null, data: { type: "smb:open" } }))
  expect(post).not.toHaveBeenCalled()
  window.dispatchEvent(new MessageEvent("message", { origin: "https://shop.example", source: window, data: { type: "smb:open", extra: "ignored" } }))
  expect(post).not.toHaveBeenCalled()
  window.dispatchEvent(new MessageEvent("message", { origin: "https://shop.example", source: window, data: { type: "smb:open" } }))
  await waitFor(() => expect(post).toHaveBeenCalledWith({ type: "smb:resize", height: expect.any(Number) }, "https://shop.example"))
  expect(name).toHaveFocus()
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
  expect(post).toHaveBeenCalledWith({ type: "smb:close" }, "https://shop.example")
  post.mockClear()
  window.dispatchEvent(new MessageEvent("message", { origin: "https://shop.example", source: window, data: { type: "smb:close" } }))
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
  expect(post).not.toHaveBeenCalled()
})
