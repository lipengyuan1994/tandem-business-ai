import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { Appointment, Resource } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { dayBoundsUtc, minutesLeft, place } from "./grid"

const TZ = "America/Denver"
const RES: Resource = { id: "r1", name: "Sam (technician)", kind: "staff", location_id: "l1", color: null, status: "active", user_id: null, version: 1, rules: [], service_ids: [] }
const appt = (over: Partial<Appointment>): Appointment => ({
  id: "a1", customer_id: "c1", customer_name: "Ada Example", service_id: "s1", service_name: "Brake service", resource_id: "r1", resource_name: RES.name,
  location_id: "l1", starts_at: "2026-11-10T18:45:00Z", ends_at: "2026-11-10T20:15:00Z", blocked_until: "2026-11-10T20:30:00Z", timezone: TZ,
  status: "confirmed", hold_expires_at: null, proposal_id: null, source: "staff", notes: null, cancelled_reason: null, version: 1, ...over,
})
const PERMS = ["scheduling.appointment.read", "scheduling.appointment.write", "scheduling.resource.read", "catalog.item.read", "crm.customer.read"]

describe("grid helpers", () => {
  it("runs in UTC like a misconfigured browser would", () => {
    expect(new Date("2026-11-10T12:00:00Z").getTimezoneOffset()).toBe(0)
  })

  it("uses the business zone for day bounds, including DST days", () => {
    expect(dayBoundsUtc("2026-11-10", TZ)).toEqual({ start: "2026-11-10T07:00:00.000Z", end: "2026-11-11T07:00:00.000Z" })
    expect(dayBoundsUtc("2026-11-01", TZ)).toEqual({ start: "2026-11-01T06:00:00.000Z", end: "2026-11-02T07:00:00.000Z" }) // 25 h
    expect(dayBoundsUtc("2026-03-08", TZ)).toEqual({ start: "2026-03-08T07:00:00.000Z", end: "2026-03-09T06:00:00.000Z" }) // 23 h
  })

  it("places an 11:45–13:15 MST appointment in local rows and counts hold minutes", () => {
    expect(place("2026-11-10T18:45:00Z", "2026-11-10T20:15:00Z", TZ)).toEqual({ top: 19, height: 6 }) // 07:00 + 19×15 min
    expect(minutesLeft("2026-11-10T17:12:00Z", new Date("2026-11-10T17:00:00Z"))).toBe(12)
    expect(minutesLeft("2026-11-10T16:00:00Z", new Date("2026-11-10T17:00:00Z"))).toBe(0)
  })
})

describe("schedule page", () => {
  function mockDay(appointments: Appointment[]) {
    const windows: string[] = []
    server.use(
      http.get("*/api/v1/scheduling/resources", () => HttpResponse.json([RES])),
      http.get("*/api/v1/scheduling/appointments", ({ request }) => {
        const u = new URL(request.url)
        windows.push(`${u.searchParams.get("start")}/${u.searchParams.get("end")}`)
        return HttpResponse.json(appointments)
      }),
    )
    return windows
  }

  it("renders appointments in the business time zone and hatches holds with a countdown", async () => {
    mockMe(meWith(...PERMS))
    const expires = new Date(Date.now() + 12 * 60_000 - 1000).toISOString()
    const windows = mockDay([appt({}), appt({ id: "a2", status: "held", starts_at: "2026-11-10T16:00:00Z", ends_at: "2026-11-10T17:30:00Z", hold_expires_at: expires, customer_name: "Dana Example" })])
    renderApp("/schedule?date=2026-11-10")
    const column = await screen.findByRole("region", { name: "Sam (technician)" })
    const confirmed = within(column).getByRole("button", { name: /^11:45–13:15 MST, Brake service, Ada Example, Confirmed$/ })
    expect(confirmed).toHaveStyle({ top: "456px", height: "144px" }) // 19 and 6 rows of 1.5rem
    const hold = within(column).getByRole("button", { name: /09:00–10:30 MST.*Dana Example, Held, hold expires in 12 min/ })
    expect(hold.className).toContain("hatched")
    expect(windows[0]).toBe("2026-11-10T07:00:00.000Z/2026-11-11T07:00:00.000Z")
  })

  it("finds slots, books one, and explains a conflict clearly", async () => {
    mockMe(meWith(...PERMS))
    mockDay([])
    let attempts = 0
    let booked: unknown = null
    server.use(
      http.get("*/api/v1/catalog/items", () => HttpResponse.json({ next_cursor: null, items: [{ id: "s1", kind: "service", name: "Brake service", status: "active", sku: null, description: null, unit: "each", currency: "USD", price_minor: 8900, duration_minutes: 90, buffer_minutes: 15, resource_kind: "staff", is_stock_tracked: false, is_bookable_online: true, version: 1 }] })),
      http.get("*/api/v1/crm/customers/match", () => HttpResponse.json([{ customer_id: "c1", display_name: "Ada Example", reason: "name", score: 0.9 }])),
      http.get("*/api/v1/scheduling/slots", () => HttpResponse.json([
        { starts_at: "2026-11-10T18:45:00Z", ends_at: "2026-11-10T20:15:00Z", resource_id: "r1", resource_name: RES.name, local_label: "Tue 10 Nov 11:45 MST" },
        { starts_at: "2026-11-10T19:00:00Z", ends_at: "2026-11-10T20:30:00Z", resource_id: "r1", resource_name: RES.name, local_label: "Tue 10 Nov 12:00 MST" },
      ])),
      http.post("*/api/v1/scheduling/appointments", async ({ request }) => {
        attempts += 1
        if (attempts === 1) return HttpResponse.json({ code: "scheduling.slot_unavailable", status: 409, title: "Conflict" }, { status: 409 })
        booked = await request.json()
        return HttpResponse.json(appt({ starts_at: "2026-11-10T19:00:00Z" }), { status: 201 })
      }),
    )
    renderApp("/schedule?date=2026-11-10")
    await userEvent.click(await screen.findByRole("button", { name: "New appointment" }))
    const dialog = await screen.findByRole("dialog")
    await userEvent.selectOptions(within(dialog).getByLabelText("Service"), "s1")
    await userEvent.type(within(dialog).getByLabelText("Customer"), "Ada")
    await userEvent.click(await within(dialog).findByRole("button", { name: "Ada Example" }))
    await userEvent.click(await within(dialog).findByRole("button", { name: "Tue 10 Nov 11:45 MST · Sam (technician)" }))
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("That time was just taken by another booking. Pick another slot.")
    await userEvent.click(within(dialog).getByRole("button", { name: "Tue 10 Nov 12:00 MST · Sam (technician)" }))
    await waitFor(() => expect(booked).toMatchObject({ customer_id: "c1", service_id: "s1", resource_id: "r1", starts_at: "2026-11-10T19:00:00Z", override_hours: false }))
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  })
})
