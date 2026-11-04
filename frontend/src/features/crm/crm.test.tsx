import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { Consent, CustomerDetail } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const CID = "00000000-0000-7000-8000-0000000000c1"
const PERMS = ["crm.customer.read", "crm.customer.write", "crm.consent.read", "crm.consent.write"]
const ADA: CustomerDetail = {
  id: CID, kind: "person", display_name: "Ada Example", given_name: null, family_name: null, organization_name: null, preferred_language: null,
  timezone: null, source: "staff", status: "active", tags: ["vip"], created_at: "2026-10-01T10:00:00Z", last_interaction_at: "2026-11-05T17:00:00Z", version: 3,
  contact_points: [{ id: "cp1", type: "email", value: "ada@example.com", is_primary: true, version: 1 }], current_consents: [],
}
const CONSENTS: Consent[] = [
  { id: "k2", channel: "email", purpose: "marketing", status: "withdrawn", source: "unsubscribe_link", evidence: "Clicked unsubscribe", recorded_at: "2026-11-02T10:00:00Z", recorded_by: null },
  { id: "k1", channel: "email", purpose: "marketing", status: "granted", source: "form", evidence: "Ticked box on web form", recorded_at: "2026-10-01T10:00:00Z", recorded_by: null },
]

function mockProfile() {
  server.use(
    http.get(`*/api/v1/crm/customers/${CID}`, () => HttpResponse.json(ADA)),
    http.get(`*/api/v1/crm/customers/${CID}/timeline`, () => HttpResponse.json([
      { id: "t1", kind: "appointment", summary: "Brake service booked for Tue 10 Nov 11:45", occurred_at: "2026-11-05T17:30:00Z", ref_id: "a1", ref_module: "scheduling", ref_type: "appointment" },
    ])),
    http.get(`*/api/v1/crm/customers/${CID}/notes`, () => HttpResponse.json([])),
    http.get(`*/api/v1/crm/customers/${CID}/consents`, () => HttpResponse.json(CONSENTS)),
  )
}

describe("customers", () => {
  it("lists customers and searches by name", async () => {
    mockMe(meWith(...PERMS))
    const queries: (string | null)[] = []
    server.use(http.get("*/api/v1/crm/customers", ({ request }) => {
      queries.push(new URL(request.url).searchParams.get("q"))
      return HttpResponse.json({ items: [{ ...ADA, primary_email: "ada@example.com", primary_phone: null }], next_cursor: null })
    }))
    renderApp("/customers")
    const row = (await screen.findByText("Ada Example")).closest("tr")!
    expect(within(row).getByText("ada@example.com")).toBeInTheDocument()
    expect(within(row).getByText("vip")).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Search customers"), "Ad")
    await waitFor(() => expect(queries).toContain("Ad"))
  })

  it("shows the timeline and the full consent history, not just the latest state", async () => {
    mockMe(meWith(...PERMS))
    mockProfile()
    renderApp(`/customers/${CID}`)
    expect(await screen.findByRole("heading", { name: "Ada Example" })).toBeInTheDocument()
    expect(await screen.findByText(/Brake service booked/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole("tab", { name: "Consent" }))
    expect(await screen.findByText(/Clicked unsubscribe/)).toBeInTheDocument()
    expect(screen.getByText(/Ticked box on web form/)).toBeInTheDocument()
    expect(screen.getAllByText("Withdrawn").length).toBeGreaterThan(0)
  })

  it("hides the consent tab without consent permission", async () => {
    mockMe(meWith("crm.customer.read"))
    mockProfile()
    renderApp(`/customers/${CID}`)
    expect(await screen.findByRole("tab", { name: "Timeline" })).toBeInTheDocument()
    expect(screen.queryByRole("tab", { name: "Consent" })).not.toBeInTheDocument()
  })

  it("validates the create dialog and creates a customer with contact points", async () => {
    mockMe(meWith(...PERMS))
    let sent: unknown = null
    server.use(
      http.get("*/api/v1/crm/customers", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.post("*/api/v1/crm/customers", async ({ request }) => { sent = await request.json(); return HttpResponse.json(ADA, { status: 201 }) }),
    )
    mockProfile()
    renderApp("/customers")
    await userEvent.click(await screen.findByRole("button", { name: "New customer" }))
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("Enter a name.")).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Name"), "Ada Example")
    await userEvent.type(screen.getByLabelText("Email"), "ada@example.com")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toMatchObject({ display_name: "Ada Example", contact_points: [{ type: "email", value: "ada@example.com", is_primary: true }] }))
  })

  it("opens the conflict dialog when someone else edited the customer", async () => {
    mockMe(meWith(...PERMS))
    mockProfile()
    let sentVersion: unknown = null
    server.use(http.patch(`*/api/v1/crm/customers/${CID}`, async ({ request }) => {
      sentVersion = ((await request.json()) as { version: number }).version
      return HttpResponse.json({ code: "crm.customer_version_conflict", status: 409, title: "Conflict", current_version: 4 }, { status: 409 })
    }))
    renderApp(`/customers/${CID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Edit customer" }))
    const name = screen.getByLabelText("Name")
    await userEvent.clear(name)
    await userEvent.type(name, "Ada Lovelace")
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("This record changed since you opened it")).toBeInTheDocument()
    expect(sentVersion).toBe(3)
  })
  it("erases personal data only after the exact name is typed, and shows what blocks it", async () => {
    mockMe(meWith(...PERMS, "admin_ops.data.manage"))
    mockProfile()
    let calls = 0
    server.use(http.post("*/api/v1/admin/erasures", async ({ request }) => {
      calls += 1
      const body = (await request.json()) as { confirm_name: string }
      if (calls === 1) return HttpResponse.json({ type: "about:blank", title: "Conflict", status: 409, code: "admin_ops.erasure_blocked", reasons: ["An open proposal mentions this customer"] }, { status: 409 })
      return HttpResponse.json({ id: "e1", customer_id: CID, status: "completed", completed_at: "2026-11-05T18:00:00Z", requested_by: null, report: { name: body.confirm_name } }, { status: 201 })
    }), http.get("*/api/v1/crm/customers", () => HttpResponse.json({ items: [], next_cursor: null })))
    renderApp(`/customers/${CID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Erase personal data…" }))
    const confirm = screen.getByRole("button", { name: "Erase personal data" })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Type “Ada Example” to confirm/), "Ada Example")
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    expect(await screen.findByText("An open proposal mentions this customer")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Erase personal data" }))
    await waitFor(() => expect(calls).toBe(2))
  })

  it("does not offer erasure without the data permission", async () => {
    mockMe(meWith(...PERMS))
    mockProfile()
    renderApp(`/customers/${CID}`)
    expect(await screen.findByRole("heading", { name: "Ada Example" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Erase personal data…" })).not.toBeInTheDocument()
  })
})

it("explains restriction and retained personal records before confirmation in regulated installs", async () => {
  mockMe(meWith(...PERMS, "admin_ops.data.manage", "admin_ops.system.read"))
  mockProfile()
  server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json({ data_profile: "regulated" })))
  renderApp(`/customers/${CID}`)
  await userEvent.click(await screen.findByRole("button", { name: "Erase personal data…" }))
  expect(await screen.findByRole("heading", { name: "Restrict Ada Example?" })).toBeInTheDocument()
  expect(screen.getByText(/Personal notes, timeline, conversation text/)).toHaveTextContent("are kept")
  expect(screen.getByText(/Lifting a restriction is not available/)).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Restrict and retain records" })).toBeDisabled()
  expect(screen.queryByRole("button", { name: "Erase personal data" })).not.toBeInTheDocument()
})

it("shows regulated restriction prerequisites and keeps the dialog open with shared blocking reasons", async () => {
  mockMe(meWith(...PERMS, "admin_ops.data.manage", "admin_ops.system.read"))
  mockProfile()
  server.use(
    http.get("*/api/v1/admin/system", () => HttpResponse.json({ data_profile: "regulated" })),
    http.post("*/api/v1/admin/erasures", () => HttpResponse.json({
      title: "Conflict", status: 409, code: "admin_ops.erasure_blocked",
      reasons: ["1 open approval request(s) mention this customer: decide or cancel them first", "1 upcoming appointment(s): cancel or complete them first"],
    }, { status: 409 })),
  )
  renderApp(`/customers/${CID}`)
  await userEvent.click(await screen.findByRole("button", { name: "Erase personal data…" }))
  expect(await screen.findByText(/Otherwise restriction is blocked and nothing is changed/)).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText(/Type “Ada Example” to confirm/), "Ada Example")
  await userEvent.click(screen.getByRole("button", { name: "Restrict and retain records" }))
  expect(await screen.findByText("Not possible yet")).toBeInTheDocument()
  expect(screen.getByText(/1 open approval request/)).toBeInTheDocument()
  expect(screen.getByText(/1 upcoming appointment/)).toBeInTheDocument()
  expect(screen.getByRole("dialog")).toBeInTheDocument()
})
