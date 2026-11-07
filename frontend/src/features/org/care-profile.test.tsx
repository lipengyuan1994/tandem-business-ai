import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it, vi } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const BUSINESS = { id: "business1", name: "Example Cycle Works", legal_name: null, timezone: "America/Denver", locale: "en-US", currency: "USD", slug: "example-cycle-works", country: "US", status: "active", public_profile: { description: "Synthetic shop", avoid_medical_details: true, booking_url: "https://example.com/book" }, version: 3 }
function setup(write = true) {
  mockMe(meWith(...(write ? ["org.business.write"] : [])))
  server.use(http.get("*/api/v1/org/business", () => HttpResponse.json(BUSINESS)))
}

describe("Customer Care public links", () => {
  it.each([
    ["Review link (optional)", "http://example.com/review"], ["Review link (optional)", "/reviews"],
    ["Review link (optional)", "javascript:alert(1)"], ["Review link (optional)", "https://"],
    ["Review link (optional)", "https://user:pass@example.com/review"], ["Booking link (optional)", "http://example.com/book"],
  ])("rejects %s = %s", async (label, value) => {
    setup()
    const patch = vi.fn()
    server.use(http.patch("*/api/v1/org/business", patch))
    renderApp("/settings/business")
    const input = await screen.findByLabelText(label)
    await userEvent.clear(input)
    await userEvent.type(input, value)
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(patch).not.toHaveBeenCalled()
    expect(await screen.findByText("Enter an absolute https URL without login details, or leave empty.")).toBeVisible()
  })
  it("saves https links with version and preserves other public profile values", async () => {
    setup()
    let sent: unknown
    server.use(http.patch("*/api/v1/org/business", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ ...BUSINESS, version: 4 }) }))
    renderApp("/settings/business")
    await userEvent.type(await screen.findByLabelText("Review link (optional)"), "https://example.com/review")
    await userEvent.clear(screen.getByLabelText("Booking link (optional)"))
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(sent).toMatchObject({ version: 3, public_profile: { ...BUSINESS.public_profile, review_url: "https://example.com/review", booking_url: null } }))
  })
  it("reloads Business settings after a version conflict", async () => {
    setup()
    let version = 3
    const versions: number[] = []
    server.use(http.get("*/api/v1/org/business", () => HttpResponse.json({ ...BUSINESS, version })), http.patch("*/api/v1/org/business", async ({ request }) => {
      versions.push((await request.json() as { version: number }).version)
      version = 5
      return versions.length === 1 ? HttpResponse.json({ title: "Conflict", status: 409, code: "org.business_version_conflict" }, { status: 409 }) : HttpResponse.json({ ...BUSINESS, version: 6 })
    }))
    renderApp("/settings/business")
    await userEvent.click(await screen.findByRole("button", { name: "Save" }))
    await userEvent.click(await screen.findByRole("button", { name: "Reload" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    await userEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(versions).toEqual([3, 5]))
  })
  it("keeps public links read-only without business write permission", async () => {
    setup(false)
    renderApp("/settings/business")
    expect(await screen.findByLabelText("Review link (optional)")).toBeDisabled()
    expect(screen.getByLabelText("Booking link (optional)")).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  })
})
