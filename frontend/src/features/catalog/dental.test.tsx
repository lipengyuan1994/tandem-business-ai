import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const notice = "Synthetic demo data. Backend-owned clinic statement."

it("creates a treatment requiring a provider and chair with a six-month recall", async () => {
  mockMe(meWith("catalog.item.read", "catalog.item.write"))
  let sent: unknown = null
  server.use(
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD" })),
    http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings: { terminology: {} }, version: 1, synthetic_demo_banner: notice })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.post("*/api/v1/catalog/items", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ id: "cleaning" }, { status: 201 }) }),
  )
  renderApp("/catalog")
  expect(await screen.findByText(notice)).toBeInTheDocument()
  await userEvent.click(await screen.findByRole("button", { name: "New service" }))
  await userEvent.type(screen.getByLabelText("Name"), "Exam and cleaning")
  await userEvent.click(screen.getByRole("checkbox", { name: "Chair (room)" }))
  expect(screen.getByRole("checkbox", { name: "Provider (staff)" })).toBeChecked()
  await userEvent.type(screen.getByLabelText(/Recall interval/), "6")
  await userEvent.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(sent).toMatchObject({ recall_interval_months: 6, required_resources: [{ role: "provider", kind: "staff" }, { role: "chair", kind: "room" }] }))
})

it("edits a treatment's required resources and recall interval", async () => {
  mockMe(meWith("catalog.item.read", "catalog.item.write"))
  let sent: unknown = null
  server.use(
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD" })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [{ id: "cleaning", name: "Exam and cleaning", kind: "service", price_minor: null, duration_minutes: 60, version: 2, status: "active", required_resources: [] }], next_cursor: null })),
    http.patch("*/api/v1/catalog/items/cleaning", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ id: "cleaning" }) }),
  )
  renderApp("/catalog")
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }))
  await userEvent.click(screen.getByRole("checkbox", { name: "Chair (room)" }))
  await userEvent.type(screen.getByLabelText(/Recall interval/), "6")
  await userEvent.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(sent).toMatchObject({ version: 2, recall_interval_months: 6, required_resources: [{ role: "provider", kind: "staff" }, { role: "chair", kind: "room" }] }))
})

it("saves emergency hand-over settings and clinic instructions", async () => {
  mockMe(meWith("org.settings.read", "org.settings.write"))
  let sent: unknown = null
  server.use(
    http.get("*/api/v1/org/settings", () => HttpResponse.json({ settings: { terminology: {}, ai_policy: "hosted_vultr", emergency_handling: false, emergency_instructions: "Call the clinic." }, version: 1 })),
    http.patch("*/api/v1/org/settings", async ({ request }) => { const body = await request.json() as Record<string, unknown>; sent = body; return HttpResponse.json(body) }),
  )
  renderApp("/settings/policies")
  await userEvent.click(await screen.findByRole("checkbox", { name: "Always hand possible emergencies to a person" }))
  const input = screen.getByLabelText("Instructions shown to visitors")
  await userEvent.clear(input)
  await userEvent.type(input, "Call the clinic immediately. Do not wait for chat.")
  await userEvent.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(sent).toMatchObject({ version: 1, settings: { emergency_handling: true, emergency_instructions: "Call the clinic immediately. Do not wait for chat." } }))
})
