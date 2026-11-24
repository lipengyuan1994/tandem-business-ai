import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const MAIN = { id: "l1", name: "Main shop", timezone: "America/Denver", is_default: true, address: null, version: 2 }
const SOUTH = { id: "l2", name: "South", timezone: "America/Denver", is_default: false, address: { street: "1 Elm St", city: "Denver" }, version: 1 }

it("renames a location, sets its address, makes another the default, and explains an edit that clashed with someone else's", async () => {
  mockMe(meWith("org.location.read", "org.location.write"))
  let locations = [MAIN, SOUTH]
  const sent: { id: string; body: Record<string, unknown> }[] = []
  server.use(
    http.get("*/api/v1/org/locations", () => HttpResponse.json(locations)),
    http.get("*/api/v1/org/locations/:id/opening-hours", () => HttpResponse.json([])),
    http.patch("*/api/v1/org/locations/:id", async ({ params, request }) => {
      const body = (await request.json()) as Record<string, unknown>
      sent.push({ id: params.id as string, body })
      if (body.name === "Clash") return HttpResponse.json({ code: "org.location_version_conflict", status: 409, title: "Conflict" }, { status: 409 })
      locations = locations.map((l) => (l.id === params.id ? { ...l, ...body, version: l.version + 1 } : body.is_default ? { ...l, is_default: false } : l)) as typeof locations
      return HttpResponse.json(locations.find((l) => l.id === params.id))
    }),
  )
  renderApp("/settings/locations")
  const form = within(await screen.findByRole("form", { name: "Location details" }))
  expect(form.getByRole("button", { name: "Save" })).toBeDisabled()
  await userEvent.clear(form.getByLabelText("Name"))
  await userEvent.type(form.getByLabelText("Name"), "Main workshop")
  await userEvent.type(form.getByLabelText("Address"), "12 Oak Ave, Denver")
  await userEvent.click(form.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(sent[0]).toEqual({ id: "l1", body: { version: 2, name: "Main workshop", timezone: "America/Denver", address: { line: "12 Oak Ave, Denver" } } }))

  await userEvent.click(screen.getByRole("button", { name: "South" }))
  const south = within(await screen.findByRole("form", { name: "Location details" }))
  expect(south.getByText("1 Elm St, Denver")).toBeInTheDocument()
  expect(south.queryByRole("textbox", { name: "Address" })).not.toBeInTheDocument()
  await userEvent.click(south.getByRole("button", { name: "Make default" }))
  await waitFor(() => expect(sent[1]).toEqual({ id: "l2", body: { version: 1, is_default: true } }))
  expect(await screen.findByText("South is now the default location")).toBeInTheDocument()

  const again = within(screen.getByRole("form", { name: "Location details" }))
  await userEvent.clear(again.getByLabelText("Name"))
  await userEvent.type(again.getByLabelText("Name"), "Clash")
  await userEvent.click(again.getByRole("button", { name: "Save" }))
  expect(await screen.findByText("This record changed since you opened it")).toBeInTheDocument()
})

it("shows location details read-only without write permission", async () => {
  mockMe(meWith("org.location.read"))
  server.use(
    http.get("*/api/v1/org/locations", () => HttpResponse.json([MAIN])),
    http.get("*/api/v1/org/locations/:id/opening-hours", () => HttpResponse.json([])),
  )
  renderApp("/settings/locations")
  expect(await screen.findByLabelText("Name")).toBeDisabled()
  expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
})
