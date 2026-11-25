import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { CustomerDetail } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const CID = "00000000-0000-7000-8000-0000000000c1"
const PERMS = ["crm.customer.read", "crm.customer.write"]
const ADA: CustomerDetail = {
  id: CID, kind: "person", display_name: "Ada Example", given_name: null, family_name: null, organization_name: null, preferred_contact_channel: "email", preferred_language: null,
  timezone: null, source: "staff", status: "active", tags: ["vip"], created_at: "2026-10-01T10:00:00Z", last_interaction_at: null, version: 3,
  contact_points: [{ id: "cp1", type: "email", value: "ada@example.com", is_primary: true, version: 1 }], current_consents: [],
}
const conflict = (code: string) => HttpResponse.json({ type: "about:blank", title: "Conflict", status: 409, code }, { status: 409, headers: { "content-type": "application/problem+json" } })

function profile(state: { customer: CustomerDetail }) {
  server.use(
    http.get(`*/api/v1/crm/customers/${CID}`, () => HttpResponse.json(state.customer)),
    http.get(`*/api/v1/crm/customers/${CID}/timeline`, () => HttpResponse.json([])),
    http.get(`*/api/v1/crm/customers/${CID}/notes`, () => HttpResponse.json([])),
  )
}

describe("customer setup", () => {
  it("adds and removes contact details, and explains a duplicate", async () => {
    mockMe(meWith(...PERMS))
    const state = { customer: ADA }
    const added: unknown[] = []
    let removed: string | null = null
    profile(state)
    server.use(
      http.post(`*/api/v1/crm/customers/${CID}/contact-points`, async ({ request }) => {
        const body = (await request.json()) as { type: "email" | "phone"; value: string; is_primary: boolean }
        added.push(body)
        if (body.value === "taken@example.com") return conflict("crm.contact_point_in_use")
        state.customer = { ...state.customer, contact_points: [...state.customer.contact_points, { id: "cp2", ...body, version: 1 }] }
        return HttpResponse.json({ id: "cp2", ...body, version: 1 }, { status: 201 })
      }),
      http.delete("*/api/v1/crm/contact-points/:id", ({ params }) => {
        removed = params.id as string
        state.customer = { ...state.customer, contact_points: state.customer.contact_points.filter((p) => p.id !== params.id) }
        return new HttpResponse(null, { status: 204 })
      }),
    )
    renderApp(`/customers/${CID}`)
    const form = within(await screen.findByRole("form", { name: "Add email or phone" }))
    await userEvent.type(form.getByLabelText("Email or phone"), "not-an-email")
    expect(form.getByRole("alert")).toHaveTextContent("Enter a valid email")
    expect(form.getByRole("button", { name: "Add" })).toBeDisabled()
    await userEvent.clear(form.getByLabelText("Email or phone"))
    await userEvent.type(form.getByLabelText("Email or phone"), "taken@example.com")
    await userEvent.click(form.getByRole("button", { name: "Add" }))
    expect(await screen.findByText("Another customer already has this email or phone.")).toBeInTheDocument()

    await userEvent.selectOptions(form.getByLabelText("Type"), "phone")
    await userEvent.clear(form.getByLabelText("Email or phone"))
    await userEvent.type(form.getByLabelText("Email or phone"), "+1 555 0100")
    await userEvent.click(form.getByLabelText("Make primary"))
    await userEvent.click(form.getByRole("button", { name: "Add" }))
    const list = within(screen.getByRole("list", { name: "Contact details" }))
    expect(await list.findByText("+1 555 0100")).toBeInTheDocument()
    expect(added.at(-1)).toEqual({ type: "phone", value: "+1 555 0100", is_primary: true })

    await userEvent.click(list.getByRole("button", { name: "Remove ada@example.com" }))
    await waitFor(() => expect(removed).toBe("cp1"))
    await waitFor(() => expect(list.queryByText("ada@example.com")).not.toBeInTheDocument())
  })

  it("adds and removes tags, lower-cased and without duplicates", async () => {
    mockMe(meWith(...PERMS))
    const state = { customer: ADA }
    const sent: string[][] = []
    profile(state)
    server.use(http.put(`*/api/v1/crm/customers/${CID}/tags`, async ({ request }) => {
      const { tags } = (await request.json()) as { tags: string[] }
      sent.push(tags)
      state.customer = { ...state.customer, tags }
      return HttpResponse.json(tags)
    }))
    renderApp(`/customers/${CID}`)
    await userEvent.type(await screen.findByLabelText("New tag"), "Fleet")
    await userEvent.click(screen.getByRole("button", { name: "Add tag" }))
    expect(await within(screen.getByRole("list", { name: "Tags" })).findByText("fleet")).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("New tag"), "VIP")
    await userEvent.click(screen.getByRole("button", { name: "Add tag" }))
    await userEvent.click(screen.getByRole("button", { name: "Remove tag vip" }))
    await waitFor(() => expect(sent).toEqual([["vip", "fleet"], ["fleet"]]))
  })

  it("archives a customer after confirming, and handles someone else's edit", async () => {
    mockMe(meWith(...PERMS))
    const state = { customer: ADA }
    let calls = 0
    profile(state)
    server.use(http.post(`*/api/v1/crm/customers/${CID}/archive`, async ({ request }) => {
      calls += 1
      const { version } = (await request.json()) as { version: number }
      if (version !== 4) return conflict("crm.customer_version_conflict")
      state.customer = { ...state.customer, status: "archived", version: 5 }
      return HttpResponse.json(state.customer)
    }))
    renderApp(`/customers/${CID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Archive" }))
    const confirm = within(await screen.findByRole("dialog", { name: "Archive Ada Example?" }))
    state.customer = { ...ADA, version: 4 }
    await userEvent.click(confirm.getByRole("button", { name: "Archive" }))
    await userEvent.click(await screen.findByRole("button", { name: "Reload" }))
    await userEvent.click(await screen.findByRole("button", { name: "Archive" }))
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Archive Ada Example?" })).getByRole("button", { name: "Archive" }))
    expect(await screen.findByText("This customer is archived.")).toBeInTheDocument()
    expect(calls).toBe(2)
    expect(screen.queryByRole("button", { name: "Archive" })).not.toBeInTheDocument()
    expect(screen.queryByRole("form", { name: "Add email or phone" })).not.toBeInTheDocument()
  })

  it("restores an archived customer", async () => {
    mockMe(meWith(...PERMS))
    const state: { customer: CustomerDetail } = { customer: { ...ADA, status: "archived", version: 5 } }
    let sent: unknown = null
    profile(state)
    server.use(http.post(`*/api/v1/crm/customers/${CID}/restore`, async ({ request }) => {
      sent = await request.json()
      state.customer = { ...state.customer, status: "active", version: 6 }
      return HttpResponse.json(state.customer)
    }))
    renderApp(`/customers/${CID}`)
    expect(await screen.findByText("This customer is archived.")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Restore" }))
    expect(await screen.findByText("Ada Example restored")).toBeInTheDocument()
    expect(sent).toEqual({ version: 5 })
    expect(await screen.findByRole("button", { name: "Archive" })).toBeInTheDocument()
  })

  it("shows contact details read-only without write permission", async () => {
    mockMe(meWith("crm.customer.read"))
    profile({ customer: ADA })
    renderApp(`/customers/${CID}`)
    expect(await within(await screen.findByRole("list", { name: "Contact details" })).findByText("ada@example.com")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Remove ada@example.com" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("New tag")).not.toBeInTheDocument()
  })
})
