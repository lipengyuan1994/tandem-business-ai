import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { server } from "@/test/server"
import { parseLocation } from "./api"
import { Widget } from "./Widget"

const BASE = "*/api/v1/public/example-cycle-works"
const profile = (ai_policy: string) => ({ name: "Example Cycle Works", timezone: "America/Denver", public_profile: {}, opening_hours: [], ai_policy })

describe("public widget", () => {
  it("parses only its own routes", () => {
    expect(parseLocation("/p/example-cycle-works/chat")).toEqual({ slug: "example-cycle-works", page: "chat" })
    expect(parseLocation("/p/example-cycle-works/request/")).toEqual({ slug: "example-cycle-works", page: "request" })
    expect(parseLocation("/p/../admin/chat")).toBeNull()
    expect(parseLocation("/today")).toBeNull()
  })

  it("states plainly when AI does not process messages", async () => {
    server.use(http.get(`${BASE}/profile`, () => HttpResponse.json(profile("strict_private"))), http.get(`${BASE}/chat`, () => HttpResponse.json({ code: "conversations.visitor_token_invalid" }, { status: 403 })))
    render(<Widget slug="example-cycle-works" page="chat" />)
    expect(await screen.findByText("Your messages are read by our staff only. They are not processed by AI.")).toBeInTheDocument()
  })

  it.each([false, true])("starts a chat and sends contact consent %s", async (consent) => {
    let started: unknown = null
    server.use(
      http.get(`${BASE}/profile`, () => HttpResponse.json(profile("hosted_vultr"))),
      http.get(`${BASE}/chat`, () => HttpResponse.json({ code: "conversations.visitor_token_invalid" }, { status: 403 })),
      http.post(`${BASE}/chat`, async ({ request }) => {
        started = await request.json()
        return HttpResponse.json({ business_name: "Example Cycle Works", conversation_id: "c1", messages: [{ id: "m1", body: "Can I come Tuesday?", from_business: false, created_at: "2026-11-05T17:00:00Z" }] }, { status: 201 })
      }),
    )
    render(<Widget slug="example-cycle-works" page="chat" />)
    expect(await screen.findByText(/hosted on Vultr/)).toBeInTheDocument()
    await userEvent.type(await screen.findByLabelText("Your name"), "Ada")
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com")
    await userEvent.type(screen.getByLabelText("Message"), "Can I come Tuesday?")
    const checkbox = screen.getByRole("checkbox", { name: "You may contact me about this request by e-mail." })
    expect(checkbox).not.toBeChecked()
    if (consent) await userEvent.click(checkbox)
    await userEvent.click(screen.getByRole("button", { name: "Send message" }))
    await waitFor(() => expect(started).toEqual({ name: "Ada", email: "ada@example.com", phone: null, message: "Can I come Tuesday?", consent_to_contact: consent }))
    expect(await screen.findByText("Can I come Tuesday?")).toBeInTheDocument()
  })

  it.each([false, true])("requires contact details for an appointment request and sends consent %s", async (consent) => {
    let sent: unknown = null
    server.use(
      http.get(`${BASE}/profile`, () => HttpResponse.json(profile("hosted_vultr"))),
      http.get(`${BASE}/services`, () => HttpResponse.json([{ id: "s1", name: "Brake service", description: null, duration_minutes: 90, price: null }])),
      http.post(`${BASE}/appointment-requests`, async ({ request }) => { sent = await request.json(); return HttpResponse.json({ received: true, message: "Thanks! We'll confirm a time soon.", reply_url: "/p/example-cycle-works/chat" }, { status: 201 }) }),
    )
    const { container } = render(<Widget slug="example-cycle-works" page="request" />)
    await screen.findByRole("option", { name: "Brake service (90 min)" })
    await userEvent.selectOptions(screen.getByLabelText("Service"), "s1")
    await userEvent.type(container.querySelector('input[type="date"]')!, "2026-11-10")
    await userEvent.type(screen.getByLabelText("Your name"), "Ada")
    await userEvent.click(screen.getByRole("button", { name: "Send request" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Please give an email or a phone number")
    await userEvent.type(screen.getByLabelText("Phone (optional)"), "+1 555 0100")
    const checkbox = screen.getByRole("checkbox", { name: "You may contact me about this request by e-mail." })
    expect(checkbox).not.toBeChecked()
    if (consent) await userEvent.click(checkbox)
    await userEvent.click(screen.getByRole("button", { name: "Send request" }))
    expect(await screen.findByRole("status")).toHaveTextContent("Thanks! We'll confirm a time soon.")
    expect(screen.getByRole("link", { name: "View replies and send a message" })).toHaveAttribute("href", "/p/example-cycle-works/chat")
    expect(sent).toMatchObject({ service_id: "s1", name: "Ada", phone: "+1 555 0100", email: null, consent_to_contact: consent, preferred: [{ date: "2026-11-10", time_from: "09:00", time_to: "12:00" }] })
  })
})

it("shows the medical-details notice above the public chat form only when requested by the server", async () => {
  server.use(
    http.get(`${BASE}/profile`, () => HttpResponse.json({ ...profile("strict_private"), avoid_medical_details: true })),
    http.get(`${BASE}/chat`, () => HttpResponse.json({ code: "conversations.visitor_token_invalid" }, { status: 403 })),
  )
  render(<Widget slug="example-cycle-works" page="chat" />)
  const notice = await screen.findByText("Please do not send medical details here. We will contact you to discuss them.")
  const name = await screen.findByLabelText("Your name")
  expect(notice.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it("polls incrementally and preserves earlier messages when sending replies", async () => {
  const { act } = await import("@testing-library/react")
  const { vi } = await import("vitest")
  let poll: (() => void) | undefined
  const interval = globalThis.setInterval
  const spy = vi.spyOn(globalThis, "setInterval").mockImplementation((callback, ms, ...args) => {
    if (ms === 5000 && typeof callback === "function") poll = callback as () => void
    return interval(callback, ms, ...args)
  })
  const cursors: (string | null)[] = []
  let sentCursor: string | null = null
  const msg = (id: string, body: string, from_business = true) => ({ id, body, from_business, created_at: `2026-11-05T17:00:0${id.slice(1)}Z` })
  const thread = (messages: ReturnType<typeof msg>[], next_cursor: string) => ({ business_name: "Example Cycle Works", conversation_id: "c1", messages, next_cursor })
  server.use(
    http.get(`${BASE}/profile`, () => HttpResponse.json(profile("strict_private"))),
    http.get(`${BASE}/chat`, ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor")
      cursors.push(cursor)
      return HttpResponse.json(cursor ? thread([msg("m2", "A new staff reply")], "cursor2") : thread([msg("m1", "An earlier message")], "cursor1"))
    }),
    http.post(`${BASE}/chat/messages`, ({ request }) => {
      sentCursor = new URL(request.url).searchParams.get("cursor")
      return HttpResponse.json(thread([msg("m3", "Visitor response", false)], "cursor3"), { status: 201 })
    }),
  )
  try {
    render(<Widget slug="example-cycle-works" page="chat" />)
    await screen.findByText("An earlier message")
    act(() => { poll?.() })
    await screen.findByText("A new staff reply")
    expect(screen.getByText("An earlier message")).toBeInTheDocument()
    expect(cursors).toEqual([null, "cursor1"])
    await userEvent.type(screen.getByLabelText("Message"), "Visitor response")
    await userEvent.click(screen.getByRole("button", { name: "Send" }))
    await screen.findByText("Visitor response")
    expect(sentCursor).toBe("cursor2")
    expect(screen.getByText("An earlier message")).toBeInTheDocument()
    expect(screen.getByText("A new staff reply")).toBeInTheDocument()
  } finally { spy.mockRestore() }
})


it("distinguishes a failed profile request from a missing business and retries", async () => {
  let attempts = 0
  server.use(http.get(`${BASE}/profile`, () => ++attempts === 1 ? HttpResponse.json({ code: "unavailable" }, { status: 503 }) : HttpResponse.json(profile("strict_private"))), http.get(`${BASE}/chat`, () => HttpResponse.json({ code: "conversations.visitor_token_invalid" }, { status: 403 })))
  render(<Widget slug="example-cycle-works" page="chat" />)
  expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong")
  expect(screen.queryByText("We couldn't find this business.")).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: "Try again" }))
  await screen.findByRole("heading", { name: "Message Example Cycle Works" })
  expect(await screen.findByRole("checkbox", { name: "You may contact me about this request by e-mail." })).toHaveAccessibleDescription(/without checking this box/)
})

it("shows service-loading failures with a retry rather than an empty picker", async () => {
  let attempts = 0
  server.use(http.get(`${BASE}/profile`, () => HttpResponse.json(profile("strict_private"))), http.get(`${BASE}/services`, () => ++attempts === 1 ? HttpResponse.json({ code: "unavailable" }, { status: 503 }) : HttpResponse.json([{ id: "s1", name: "Brake service", duration_minutes: 90 }])))
  render(<Widget slug="example-cycle-works" page="request" />)
  expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong")
  expect(screen.getByRole("button", { name: "Send request" })).toBeDisabled()
  await userEvent.click(screen.getByRole("button", { name: "Try again" }))
  await screen.findByRole("option", { name: "Brake service (90 min)" })
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Send request" })).toBeEnabled()
})

it("shows a failed chat resume and clears the error after a successful retry", async () => {
  let attempts = 0
  server.use(http.get(`${BASE}/profile`, () => HttpResponse.json(profile("strict_private"))), http.get(`${BASE}/chat`, () => ++attempts === 1 ? HttpResponse.json({ code: "unavailable" }, { status: 503 }) : HttpResponse.json({ conversation_id: "c1", messages: [{ id: "m1", body: "A saved message", from_business: true, created_at: "2026-11-05T17:00:00Z" }] })))
  render(<Widget slug="example-cycle-works" page="chat" />)
  expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong")
  await userEvent.click(screen.getByRole("button", { name: "Try again" }))
  await screen.findByText("A saved message")
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})
