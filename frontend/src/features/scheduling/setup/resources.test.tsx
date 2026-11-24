import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { CatalogItem, Resource, Schemas } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { rulesProblem } from "./rules"

const READ = ["scheduling.resource.read", "catalog.item.read", "org.location.read"]
const WRITE = [...READ, "scheduling.resource.write"]
const SAM: Resource = {
  id: "r1", name: "Sam Example", kind: "staff", location_id: "l1", color: null, status: "active", user_id: null, version: 3,
  rules: [{ weekday: 0, start_time: "09:00:00", end_time: "17:00:00" }], service_ids: ["s1"],
}
const BAY: Resource = { ...SAM, id: "r2", name: "Service bay 2", kind: "room", status: "archived", rules: [], service_ids: [] }
const service = (over: Partial<CatalogItem>): CatalogItem => ({
  id: "s1", kind: "service", name: "Brake service", status: "active", sku: null, description: null, unit: "each", currency: "USD", price_minor: 8900,
  duration_minutes: 60, buffer_minutes: 15, resource_kind: "staff", is_stock_tracked: false, is_bookable_online: true, version: 1, ...over,
})

function mockSetup(resources: Resource[], exceptions: Schemas["ExceptionOut"][] = []) {
  const queries: string[] = []
  server.use(
    http.get("*/api/v1/scheduling/resources", ({ request }) => { queries.push(new URL(request.url).search); return HttpResponse.json(resources) }),
    http.get("*/api/v1/scheduling/exceptions", () => HttpResponse.json(exceptions)),
    http.get("*/api/v1/org/locations", () => HttpResponse.json([{ id: "l1", name: "Main shop", is_default: true }])),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ next_cursor: null, items: [service({}), service({ id: "s2", name: "Bike fitting" }), service({ id: "s3", name: "Wheel truing", resource_kind: "equipment" })] })),
  )
  return queries
}

describe("weekly hours checks", () => {
  it("flags an end before its start and overlapping ranges on one day", () => {
    expect(rulesProblem([{ weekday: 0, start_time: "09:00", end_time: "12:00" }, { weekday: 0, start_time: "13:00", end_time: "17:00" }])).toBeNull()
    expect(rulesProblem([{ weekday: 2, start_time: "17:00", end_time: "09:00" }])).toEqual({ key: "resources.hours.endBeforeStart", day: 2 })
    expect(rulesProblem([{ weekday: 1, start_time: "09:00", end_time: "13:00" }, { weekday: 1, start_time: "12:00", end_time: "17:00" }])).toEqual({ key: "resources.hours.overlap", day: 1 })

    // Different seasons never apply on the same date, so they may overlap in time.
    expect(rulesProblem([
      { weekday: 0, start_time: "09:00", end_time: "17:00", valid_from: "2027-01-01", valid_to: "2027-06-30" },
      { weekday: 0, start_time: "10:00", end_time: "18:00", valid_from: "2027-07-01" },
    ])).toBeNull()
    expect(rulesProblem([
      { weekday: 0, start_time: "09:00", end_time: "17:00", valid_to: "2027-07-15" },
      { weekday: 0, start_time: "10:00", end_time: "18:00", valid_from: "2027-07-01" },
    ])).toEqual({ key: "resources.hours.overlap", day: 0 })
  })
})

describe("settings → resources", () => {
  it("lists active and archived resources read-only without the write permission", async () => {
    mockMe(meWith(...READ))
    const queries = mockSetup([BAY, SAM])
    renderApp("/settings/resources")
    const list = await screen.findByRole("list", { name: "Resources" })
    const items = within(list).getAllByRole("button")
    expect(items.map((b) => b.textContent)).toEqual(["Sam Example", "Service bay 2Archived"])
    expect(queries[0]).toContain("include_archived=true")
    expect(screen.getByRole("link", { name: "Resources & availability" })).toHaveAttribute("aria-current", "page")
    expect(await screen.findByLabelText("Start time 1 on Monday")).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Add resource" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save weekly hours" })).not.toBeInTheDocument()
  })

  it("adds a resource and selects it", async () => {
    mockMe(meWith(...WRITE))
    const resources = [SAM]
    mockSetup(resources)
    let created: unknown = null
    server.use(http.post("*/api/v1/scheduling/resources", async ({ request }) => {
      created = await request.json()
      const r: Resource = { ...SAM, id: "r9", name: "Lift 1", kind: "equipment", rules: [], service_ids: [], version: 1 }
      resources.push(r)
      return HttpResponse.json(r, { status: 201 })
    }))
    renderApp("/settings/resources")
    const form = await screen.findByRole("form", { name: "New resource" })
    await userEvent.type(within(form).getByLabelText("Name"), "Lift 1")
    await userEvent.selectOptions(within(form).getByLabelText("Type"), "equipment")
    await userEvent.click(within(form).getByRole("button", { name: "Add resource" }))
    await waitFor(() => expect(created).toEqual({ name: "Lift 1", kind: "equipment", location_id: null }))
    expect(await screen.findByRole("button", { name: "Lift 1", pressed: true })).toBeInTheDocument()
  })

  it("saves weekly hours with a lunch break and blocks invalid ranges", async () => {
    mockMe(meWith(...WRITE))
    mockSetup([SAM])
    let saved: unknown = null
    server.use(http.put("*/api/v1/scheduling/resources/r1/availability", async ({ request }) => { saved = await request.json(); return HttpResponse.json(SAM) }))
    renderApp("/settings/resources")
    const end = await screen.findByLabelText("End time 1 on Monday")
    await userEvent.clear(end)
    await userEvent.type(end, "12:00")
    await userEvent.click(screen.getByRole("button", { name: "Add hours on Monday" }))
    expect(screen.getByLabelText("Start time 2 on Monday")).toHaveValue("12:00")
    expect(screen.getByLabelText("End time 2 on Monday")).toHaveValue("18:00")
    const start2 = screen.getByLabelText("Start time 2 on Monday")
    await userEvent.clear(start2)
    await userEvent.type(start2, "11:00")
    expect(screen.getByRole("alert")).toHaveTextContent("On Monday, two time ranges overlap.")
    expect(screen.getByRole("button", { name: "Save weekly hours" })).toBeDisabled()
    await userEvent.clear(start2)
    await userEvent.type(start2, "13:00")
    await userEvent.click(screen.getByRole("button", { name: "Add hours on Wednesday" }))
    await userEvent.click(screen.getByRole("button", { name: "Save weekly hours" }))
    await waitFor(() => expect(saved).toEqual([
      { weekday: 0, start_time: "09:00", end_time: "12:00" },
      { weekday: 0, start_time: "13:00", end_time: "18:00" },
      { weekday: 2, start_time: "09:00", end_time: "17:00" },
    ]))
  })

  it("adds a valid interval after a day that already runs past six", async () => {
    mockMe(meWith(...WRITE))
    mockSetup([{ ...SAM, rules: [{ weekday: 0, start_time: "09:00:00", end_time: "19:00:00" }] }])
    renderApp("/settings/resources")
    await userEvent.click(await screen.findByRole("button", { name: "Add hours on Monday" }))
    expect(screen.getByLabelText("Start time 2 on Monday")).toHaveValue("19:00")
    expect(screen.getByLabelText("End time 2 on Monday")).toHaveValue("20:00")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("copies Monday's hours to the other weekdays", async () => {
    mockMe(meWith(...WRITE))
    mockSetup([SAM])
    let saved: Schemas["RuleIn"][] = []
    server.use(http.put("*/api/v1/scheduling/resources/r1/availability", async ({ request }) => { saved = (await request.json()) as Schemas["RuleIn"][]; return HttpResponse.json(SAM) }))
    renderApp("/settings/resources")
    await userEvent.click(await screen.findByRole("button", { name: "Copy Monday to Tuesday–Friday" }))
    expect(screen.getByLabelText("Start time 1 on Friday")).toHaveValue("09:00")
    expect(screen.queryByLabelText("Start time 1 on Saturday")).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Save weekly hours" }))
    await waitFor(() => expect(saved.map((r) => r.weekday)).toEqual([0, 1, 2, 3, 4]))
  })

  it("ticks the services a resource can do and notes a kind mismatch", async () => {
    mockMe(meWith(...WRITE))
    mockSetup([SAM])
    let saved: unknown = null
    server.use(http.put("*/api/v1/scheduling/resources/r1/services", async ({ request }) => { saved = await request.json(); return HttpResponse.json(SAM) }))
    renderApp("/settings/resources")
    const fieldset = await screen.findByRole("group", { name: /Tick the services/ })
    expect(within(fieldset).getByRole("checkbox", { name: /Brake service/ })).toBeChecked()
    expect(within(fieldset).getByText("(needs: Equipment)")).toBeInTheDocument()
    const save = screen.getByRole("button", { name: "Save services" })
    expect(save).toBeDisabled()
    await userEvent.click(within(fieldset).getByRole("checkbox", { name: /Bike fitting/ }))
    await userEvent.click(save)
    await waitFor(() => expect(saved).toEqual(["s1", "s2"]))
  })

  it("renames and archives with the current version, and shows the conflict dialog on a stale version", async () => {
    mockMe(meWith(...WRITE))
    mockSetup([SAM])
    const bodies: unknown[] = []
    server.use(http.patch("*/api/v1/scheduling/resources/r1", async ({ request }) => {
      bodies.push(await request.json())
      if (bodies.length === 2) return HttpResponse.json({ code: "scheduling.resource_version_conflict", status: 409, title: "Conflict" }, { status: 409 })
      return HttpResponse.json(SAM)
    }))
    renderApp("/settings/resources")
    const name = await screen.findByLabelText("Name", { selector: "#resource-name" })
    await userEvent.clear(name)
    await userEvent.type(name, "Sam A. Example")
    await userEvent.click(screen.getByRole("button", { name: "Rename" }))
    await waitFor(() => expect(bodies[0]).toEqual({ version: 3, name: "Sam A. Example" }))
    await userEvent.click(screen.getByRole("button", { name: "Archive" }))
    await waitFor(() => expect(bodies[1]).toEqual({ version: 3, status: "archived" }))
    expect(await screen.findByRole("dialog")).toHaveTextContent(/changed/i)
  })

  it("adds a whole-day closure in the business time zone and removes one", async () => {
    mockMe(meWith(...WRITE))
    const upcoming = { id: "x1", resource_id: "r1", kind: "closed" as const, reason: "Training", starts_at: "2099-01-05T16:00:00Z", ends_at: "2099-01-05T20:00:00Z" }
    const past = { ...upcoming, id: "x0", reason: "Old", starts_at: "2020-01-01T16:00:00Z", ends_at: "2020-01-01T20:00:00Z" }
    mockSetup([SAM], [upcoming, past])
    let added: unknown = null
    let removed = ""
    server.use(
      http.post("*/api/v1/scheduling/exceptions", async ({ request }) => { added = await request.json(); return HttpResponse.json({ ...upcoming, id: "x2" }, { status: 201 }) }),
      http.delete("*/api/v1/scheduling/exceptions/:id", ({ params }) => { removed = String(params.id); return new HttpResponse(null, { status: 204 }) }),
    )
    renderApp("/settings/resources")
    const list = await screen.findByRole("list", { name: "Upcoming closures and extra openings" })
    expect(within(list).getAllByRole("listitem")).toHaveLength(1)
    expect(list).toHaveTextContent("Mon 5 Jan 2099, 09:00 – Mon 5 Jan 2099, 13:00")
    expect(list).toHaveTextContent("Sam Example")
    await userEvent.click(within(list).getByRole("button", { name: "Remove the one starting Mon 5 Jan 2099, 09:00" }))
    await waitFor(() => expect(removed).toBe("x1"))

    const form = screen.getByRole("form", { name: "Add closure or opening" })
    const from = within(form).getByLabelText("From")
    await userEvent.clear(from)
    await userEvent.type(from, "2099-12-24")
    const to = within(form).getByLabelText("To")
    await userEvent.clear(to)
    await userEvent.type(to, "2099-12-26")
    await userEvent.type(within(form).getByLabelText("Reason (optional)"), "Holidays")
    await userEvent.click(within(form).getByRole("button", { name: "Add closure or opening" }))
    // Denver is UTC−7 in December: midnight on the 24th to midnight after the 26th.
    await waitFor(() => expect(added).toEqual({ kind: "closed", resource_id: null, reason: "Holidays", starts_at: "2099-12-24T07:00:00.000Z", ends_at: "2099-12-27T07:00:00.000Z" }))
  })

  it("enters a closure in the time zone of the resource's location and names the location everyone means", async () => {
    mockMe(meWith(...WRITE))
    mockSetup([{ ...SAM, location_id: "l2" }])
    server.use(http.get("*/api/v1/org/locations", () => HttpResponse.json([
      { id: "l1", name: "Main shop", is_default: true, timezone: "America/Denver", address: {}, version: 1 },
      { id: "l2", name: "East shop", is_default: false, timezone: "America/New_York", address: {}, version: 1 },
    ])))
    let added: unknown = null
    server.use(http.post("*/api/v1/scheduling/exceptions", async ({ request }) => { added = await request.json(); return HttpResponse.json({ id: "x2", ...(added as object) }, { status: 201 }) }))
    renderApp("/settings/resources")
    const form = await screen.findByRole("form", { name: "Add closure or opening" })
    expect(await within(form).findByRole("option", { name: "Everyone at Main shop" })).toBeInTheDocument()
    expect(within(form).queryByText(/Times for this one are in/)).not.toBeInTheDocument()
    await userEvent.selectOptions(within(form).getByLabelText("Applies to"), "Sam Example")
    expect(within(form).getByText("Times for this one are in America/New_York, the time zone of its location.")).toBeInTheDocument()
    const from = within(form).getByLabelText("From")
    await userEvent.clear(from)
    await userEvent.type(from, "2099-12-24")
    const to = within(form).getByLabelText("To")
    await userEvent.clear(to)
    await userEvent.type(to, "2099-12-24")
    await userEvent.click(within(form).getByRole("button", { name: "Add closure or opening" }))
    // New York is UTC−5 in December.
    await waitFor(() => expect(added).toMatchObject({ resource_id: "r1", starts_at: "2099-12-24T05:00:00.000Z", ends_at: "2099-12-25T05:00:00.000Z" }))
  })

  it("asks who an extra opening is for, since one for everyone would open no slots", async () => {
    mockMe(meWith(...WRITE))
    mockSetup([SAM])
    let sent: unknown = null
    server.use(http.post("*/api/v1/scheduling/exceptions", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ id: "x1", ...(sent as object) }, { status: 201 }) }))
    renderApp("/settings/resources")
    const form = await screen.findByRole("form", { name: "Add closure or opening" })
    await userEvent.selectOptions(within(form).getByLabelText("Type"), "extra_open")
    expect(within(form).getByRole("alert")).toHaveTextContent("Choose who the extra opening is for.")
    expect(within(form).getByRole("button", { name: "Add closure or opening" })).toBeDisabled()
    await userEvent.selectOptions(within(form).getByLabelText("Applies to"), "Sam Example")
    expect(within(form).queryByRole("alert")).not.toBeInTheDocument()
    await userEvent.click(within(form).getByRole("button", { name: "Add closure or opening" }))
    await waitFor(() => expect(sent).toMatchObject({ kind: "extra_open", resource_id: "r1" }))
  })

  it("says what nothing ticked means and never sends links to archived services", async () => {
    mockMe(meWith(...WRITE))
    const NEW: Resource = { ...SAM, id: "r3", name: "Alex Example", service_ids: [] }
    mockSetup([{ ...SAM, service_ids: ["s1", "gone"] }, NEW])
    let saved: unknown = null
    server.use(http.put("*/api/v1/scheduling/resources/r1/services", async ({ request }) => { saved = await request.json(); return HttpResponse.json(SAM) }))
    renderApp("/settings/resources")
    await userEvent.click(await screen.findByRole("button", { name: /Alex Example/ }))
    expect(await screen.findByText("Nothing is ticked, so this resource can be booked for any service it fits.")).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: /Sam Example/ }))
    await waitFor(() => expect(screen.queryByText(/Nothing is ticked/)).not.toBeInTheDocument())
    const fieldset = screen.getByRole("group", { name: /Tick the services/ })
    expect(within(fieldset).getByText("Bike fitting").parentElement).toHaveTextContent("nobody is ticked for this, so anyone who fits can do it")
    expect(within(fieldset).getByText("Brake service").parentElement).not.toHaveTextContent("nobody is ticked")
    expect(screen.getByRole("button", { name: "Save services" })).toBeDisabled()
    await userEvent.click(within(fieldset).getByRole("checkbox", { name: /Bike fitting/ }))
    await userEvent.click(screen.getByRole("button", { name: "Save services" }))
    await waitFor(() => expect(saved).toEqual(["s1", "s2"]))
  })

  it("refuses a timed opening that ends before it starts", async () => {
    mockMe(meWith(...WRITE))
    mockSetup([SAM])
    renderApp("/settings/resources")
    const form = await screen.findByRole("form", { name: "Add closure or opening" })
    await userEvent.selectOptions(within(form).getByLabelText("Type"), "extra_open")
    await userEvent.selectOptions(within(form).getByLabelText("Applies to"), "Sam Example")
    await userEvent.click(within(form).getByLabelText("Whole days"))
    const endTime = within(form).getByLabelText("End time")
    await userEvent.clear(endTime)
    await userEvent.type(endTime, "08:00")
    expect(within(form).getByRole("alert")).toHaveTextContent("The end must be after the start.")
    expect(within(form).getByRole("button", { name: "Add closure or opening" })).toBeDisabled()
  })
})
