import { StrictMode } from "react"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, expect, it } from "vitest"
import { server } from "@/test/server"
import { Bookings } from "./Bookings"

const BASE = "*/api/v1/public/example-cycle-works/self-service"
const row = { id: "b1", service_name: "Brake service", starts_at: "2030-11-10T17:00:00Z", ends_at: "2030-11-10T18:00:00Z", timezone: "America/Denver", local_time: "Sun 10 Nov 10:00 MST", location: "Example workshop", staff_name: null, status: "confirmed", version: 1, fee_required: false, fee_text: null }
afterEach(() => history.replaceState(null, "", "/"))
function renderBookings() { return render(<StrictMode><Bookings slug="example-cycle-works" /></StrictMode>) }
function list(items: unknown[] = [row]) { server.use(http.get(`${BASE}/bookings`, () => HttpResponse.json({ items }))) }

it("shows request form and neutral reply without exposing links", async () => {
  server.use(http.get(`${BASE}/bookings`, () => HttpResponse.json({ code: "self_service.session_invalid" }, { status: 403 })), http.post(`${BASE}/request`, () => HttpResponse.json({ message: "neutral" }, { status: 202 })))
  renderBookings()
  await userEvent.type(await screen.findByLabelText("Email"), "ben@example.com")
  await userEvent.click(screen.getByRole("button", { name: "Email me a sign-in link" }))
  expect(await screen.findByRole("status")).toHaveTextContent("If eligible bookings match")
  expect(screen.queryByRole("link")).not.toBeInTheDocument()
})

it("removes the fragment before a single POST exchange under StrictMode, then lists safe booking fields", async () => {
  history.replaceState(null, "", `/p/example-cycle-works/bookings#t=${"a".repeat(43)}`)
  let exchanges = 0
  server.use(http.post(`${BASE}/exchange`, async ({ request }) => {
    exchanges++; expect(location.hash).toBe(""); expect(request.url).not.toContain("#")
    expect(await request.json()).toEqual({ token: "a".repeat(43) })
    return HttpResponse.json({ authenticated: true })
  }))
  list(); renderBookings()
  expect(await screen.findByText("Brake service")).toBeInTheDocument()
  expect(screen.getByText(/Sun 10 Nov 10:00 MST/)).toHaveTextContent("Example workshop")
  expect(exchanges).toBe(1)
  expect(screen.getByRole("heading", { name: "My bookings" })).toHaveFocus()
})

it.each(["expired", "used"])("handles %s link and permits requesting another", async () => {
  history.replaceState(null, "", `/p/example-cycle-works/bookings#t=${"b".repeat(43)}`)
  server.use(http.post(`${BASE}/exchange`, () => HttpResponse.json({ code: "self_service.link_invalid" }, { status: 403 })))
  renderBookings()
  expect(await screen.findByRole("alert")).toHaveTextContent("expired, was already used")
  expect(screen.getByLabelText("Email")).toBeInTheDocument()
  expect(location.hash).toBe("")
})

it("handles empty bookings", async () => {
  list([]); renderBookings()
  expect(await screen.findByText("You have no upcoming bookings.")).toBeInTheDocument()
})

it.each([429, 500])("shows request failure %s", async (status) => {
  server.use(http.get(`${BASE}/bookings`, () => HttpResponse.json({ code: "self_service.session_invalid" }, { status: 403 })), http.post(`${BASE}/request`, () => HttpResponse.json({ code: "failed" }, { status })))
  renderBookings(); await userEvent.type(await screen.findByLabelText("Email"), "ben@example.com")
  await userEvent.click(screen.getByRole("button", { name: "Email me a sign-in link" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(status === 429 ? "Too many requests" : "couldn't load")
})

it("requires fee acknowledgment and records the displayed policy on cancellation", async () => {
  list([{ ...row, fee_required: true, fee_text: "A $10 fee applies" }])
  let sent: unknown
  server.use(http.post(`${BASE}/bookings/b1/cancel`, async ({ request }) => { sent = await request.json(); return HttpResponse.json({ ...row, status: "cancelled" }) }))
  renderBookings(); await userEvent.click(await screen.findByRole("button", { name: "Cancel" }))
  expect(screen.getByText("A $10 fee applies")).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Confirm cancellation" })).toBeDisabled()
  await userEvent.click(screen.getByRole("checkbox", { name: "I acknowledge this fee policy." }))
  await userEvent.click(screen.getByRole("button", { name: "Confirm cancellation" }))
  expect(await screen.findByText(/Your booking was updated/)).toBeInTheDocument()
  expect(sent).toEqual({ version: 1, fee_acknowledged: true, acknowledged_fee_text: "A $10 fee applies" })
})

it("cancels outside the notice window without acknowledgment and supports keeping a booking", async () => {
  list(); let changes = 0
  server.use(http.post(`${BASE}/bookings/b1/cancel`, () => { changes++; return HttpResponse.json({ ...row, status: "cancelled" }) }))
  renderBookings(); await userEvent.click(await screen.findByRole("button", { name: "Cancel" }))
  await userEvent.click(screen.getByRole("button", { name: "Keep booking" }))
  expect(changes).toBe(0)
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }))
  await userEvent.click(screen.getByRole("button", { name: "Confirm cancellation" }))
  await screen.findByText(/Your booking was updated/); expect(changes).toBe(1)
})

it("finds slots and reschedules the original booking", async () => {
  list(); let sent: unknown
  server.use(http.get(`${BASE}/bookings/b1/slots`, () => HttpResponse.json([{ starts_at: "2030-11-11T17:00:00Z", ends_at: "2030-11-11T18:00:00Z", resource_id: "r1", local_label: "Mon 11 Nov 10:00 MST" }])), http.post(`${BASE}/bookings/b1/reschedule`, async ({ request }) => { sent = await request.json(); return HttpResponse.json(row) }))
  renderBookings(); await userEvent.click(await screen.findByRole("button", { name: "Reschedule" }))
  await userEvent.type(screen.getByLabelText("Search from date"), "2030-11-11")
  await userEvent.click(screen.getByRole("button", { name: "Find available times" }))
  await userEvent.selectOptions(await screen.findByLabelText("Available time"), "0")
  await userEvent.click(screen.getByRole("button", { name: "Confirm reschedule" }))
  await screen.findByText(/Your booking was updated/)
  expect(sent).toMatchObject({ version: 1, starts_at: "2030-11-11T17:00:00Z", resource_id: "r1" })
})

it.each(["empty", "error"])("shows %s slot search", async (state) => {
  list(); server.use(http.get(`${BASE}/bookings/b1/slots`, () => state === "empty" ? HttpResponse.json([]) : HttpResponse.json({ code: "error" }, { status: 500 })))
  renderBookings(); await userEvent.click(await screen.findByRole("button", { name: "Reschedule" }))
  await userEvent.type(screen.getByLabelText("Search from date"), "2030-11-11")
  await userEvent.click(screen.getByRole("button", { name: "Find available times" }))
  if (state === "empty") expect(await screen.findByText(/No available times/)).toBeInTheDocument()
  else expect(await screen.findByRole("alert")).toHaveTextContent("couldn't load")
})

it.each([409, 403, 422, 500])("handles booking change status %s with safe recovery", async (status) => {
  list(); let reads = 0
  server.use(http.get(`${BASE}/bookings`, () => { reads++; return HttpResponse.json({ items: [{ ...row, version: reads }] }) }), http.post(`${BASE}/bookings/b1/cancel`, () => HttpResponse.json({ code: status === 422 ? "self_service.fee_acknowledgment_required" : "failed" }, { status })))
  renderBookings(); await userEvent.click(await screen.findByRole("button", { name: "Cancel" }))
  await userEvent.click(screen.getByRole("button", { name: "Confirm cancellation" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(status === 409 ? "reloaded" : status === 403 ? "expired" : status === 422 ? "acknowledge" : "couldn't load")
  if (status === 409) await waitFor(() => expect(reads).toBe(2))
  if (status === 403) expect(screen.getByLabelText("Email")).toBeInTheDocument()
})

it("does not offer changes for cancelled bookings", async () => {
  list([{ ...row, status: "cancelled" }]); renderBookings()
  await screen.findByText("Brake service"); expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument()
})

it("reloads the fee policy when a notice window begins while the page is open", async () => {
  let reads = 0
  server.use(http.get(`${BASE}/bookings`, () => { reads++; return HttpResponse.json({ items: [{ ...row, fee_required: reads > 1, fee_text: reads > 1 ? "Current fee" : null }] }) }), http.post(`${BASE}/bookings/b1/cancel`, () => HttpResponse.json({ code: "self_service.fee_acknowledgment_required" }, { status: 422 })))
  renderBookings(); await userEvent.click(await screen.findByRole("button", { name: "Cancel" }))
  await userEvent.click(screen.getByRole("button", { name: "Confirm cancellation" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("acknowledge")
  await waitFor(() => expect(reads).toBe(2))
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(screen.getByText("Current fee")).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Confirm cancellation" })).toBeDisabled()
})

it("exchanges a new link opened as a fragment navigation in an existing bookings tab", async () => {
  let exchanged = false
  server.use(http.get(`${BASE}/bookings`, () => HttpResponse.json({ items: [{ ...row, service_name: exchanged ? "New link service" : "Previous booking" }] })), http.post(`${BASE}/exchange`, async ({ request }) => { expect(await request.json()).toEqual({ token: "c".repeat(43) }); exchanged = true; return HttpResponse.json({ authenticated: true }) }))
  renderBookings(); await screen.findByText("Previous booking")
  location.hash = `#t=${"c".repeat(43)}`
  expect(await screen.findByText("New link service")).toBeInTheDocument()
  expect(screen.queryByText("Previous booking")).not.toBeInTheDocument()
  expect(location.hash).toBe("")
})

it("ignores a late bookings response from before the new link exchange", async () => {
  let exchanged = false
  let started = false
  let release = () => {}
  const old = new Promise<void>((resolve) => { release = resolve })
  server.use(http.get(`${BASE}/bookings`, async () => {
    if (!exchanged) { started = true; await old; return HttpResponse.json({ items: [{ ...row, service_name: "Previous customer" }] }) }
    return HttpResponse.json({ items: [{ ...row, service_name: "Current customer" }] })
  }), http.post(`${BASE}/exchange`, () => { exchanged = true; return HttpResponse.json({ authenticated: true }) }))
  renderBookings(); await waitFor(() => expect(started).toBe(true))
  location.hash = `#t=${"d".repeat(43)}`
  try { expect(await screen.findByText("Current customer")).toBeInTheDocument() } finally { release() }
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
  expect(screen.queryByText("Previous customer")).not.toBeInTheDocument()
  expect(screen.getByText("Current customer")).toBeInTheDocument()
})
