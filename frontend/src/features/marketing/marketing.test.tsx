import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { AudiencePreview, CampaignDetail } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const CID = "00000000-0000-7000-8000-0000000000c9"
const SEG = "00000000-0000-7000-8000-0000000000e9"
const PERMS = ["marketing.campaign.read", "marketing.campaign.write", "catalog.item.read"]
const REV = { id: "r1", revision_no: 1, subject: "Spring service offer", body_markdown: "10% off a tune-up.\n\nBook online.", created_by_kind: "staff", created_at: "2026-11-05T17:00:00Z" }

function campaign(over: Partial<CampaignDetail> = {}): CampaignDetail {
  return { id: CID, name: "Spring offer", purpose: null, channel: "email", segment_id: SEG, status: "draft", current_revision: REV, snapshot: null, proposal_id: null,
    sent_at: null, version: 1, created_at: "2026-11-05T17:00:00Z", revisions: [REV], ...over } as CampaignDetail
}
const PREVIEW: AudiencePreview = { matched: 5, eligible_count: 2, excluded_counts: { no_consent: 2, suppressed: 1, no_contact: 0, archived: 0 },
  sample: [], offset: 0, members: [{ customer_id: "c1", display_name: "Ada Example" }, { customer_id: "c2", display_name: "Ben Example" }], hash: "a".repeat(64) }

function mockCampaign(c: CampaignDetail) {
  server.use(
    http.get(`*/api/v1/marketing/campaigns/${CID}`, () => HttpResponse.json(c)),
    http.get(`*/api/v1/marketing/segments/${SEG}/preview`, () => HttpResponse.json(PREVIEW)),
  )
}

describe("marketing", () => {
  it("lists campaigns with their status", async () => {
    mockMe(meWith(...PERMS))
    server.use(http.get("*/api/v1/marketing/campaigns", () => HttpResponse.json([campaign()])))
    renderApp("/marketing")
    expect(await screen.findByText("Spring offer")).toBeInTheDocument()
    expect(screen.getByText(/Spring service offer/)).toBeInTheDocument()
    expect(screen.getByText("Draft")).toBeInTheDocument()
  })

  it("shows who would receive it and why others are excluded", async () => {
    mockMe(meWith(...PERMS))
    mockCampaign(campaign())
    renderApp(`/marketing/${CID}`)
    expect(await screen.findByText("people would receive this")).toBeInTheDocument()
    const excluded = screen.getByRole("list", { name: "Excluded" })
    expect(within(excluded).getByText("2 have no marketing consent")).toBeInTheDocument()
    expect(within(excluded).getByText("1 unsubscribed or suppressed")).toBeInTheDocument()
    expect(within(screen.getByRole("list", { name: "Recipients (page)" })).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Ada Example", "Ben Example"])
  })

  it("builds an audience from whitelisted filters and creates the draft", async () => {
    mockMe(meWith(...PERMS))
    let segment: unknown = null
    let created: unknown = null
    server.use(
      http.get("*/api/v1/marketing/campaigns", () => HttpResponse.json([])),
      http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [], next_cursor: null })),
      http.post("*/api/v1/marketing/segments", async ({ request }) => { segment = await request.json(); return HttpResponse.json({ id: SEG, name: "x", definition: {}, version: 1, created_at: "2026-11-05T17:00:00Z" }, { status: 201 }) }),
      http.post("*/api/v1/marketing/campaigns", async ({ request }) => { created = await request.json(); return HttpResponse.json(campaign(), { status: 201 }) }),
    )
    mockCampaign(campaign())
    renderApp("/marketing")
    await userEvent.click(await screen.findByRole("button", { name: "New campaign" }))
    const dialog = await screen.findByRole("dialog")
    const create = within(dialog).getByRole("button", { name: "Create draft" })
    expect(create).toBeDisabled() // needs a name, a message and at least one filter
    await userEvent.type(within(dialog).getByLabelText("Campaign name"), "Spring offer")
    await userEvent.selectOptions(within(dialog).getByLabelText("Add a filter"), "last_interaction_older")
    await userEvent.type(within(dialog).getByLabelText("Days"), "180")
    await userEvent.click(within(dialog).getByRole("button", { name: "Add filter" }))
    await userEvent.selectOptions(within(dialog).getByLabelText("Add a filter"), "tag")
    await userEvent.type(within(dialog).getByLabelText("Value"), "road-bike")
    await userEvent.click(within(dialog).getByRole("button", { name: "Add filter" }))
    expect(within(dialog).getByText("No contact for 180+ days")).toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText("Subject"), "Spring service offer")
    await userEvent.type(within(dialog).getByLabelText("Message"), "10% off a tune-up.")
    await userEvent.click(create)
    await waitFor(() => expect(segment).toEqual({ name: "Spring offer", definition: { all: [
      { field: "last_interaction_at", op: "older_than_days", value: 180 }, { field: "tag", op: "has", value: "road-bike" }] } }))
    expect(created).toMatchObject({ segment_id: SEG, subject: "Spring service offer", body_markdown: "10% off a tune-up." })
  })

  it("submits for approval and links to the approval once frozen", async () => {
    mockMe(meWith(...PERMS))
    let submitted = 0
    mockCampaign(campaign())
    server.use(http.post(`*/api/v1/marketing/campaigns/${CID}/submit`, () => { submitted += 1; return HttpResponse.json(campaign({ status: "in_review", proposal_id: "p1" })) }))
    renderApp(`/marketing/${CID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Submit for approval" }))
    await waitFor(() => expect(submitted).toBe(1))
  })

  it("warns that editing a message under review withdraws the approval request", async () => {
    mockMe(meWith(...PERMS))
    mockCampaign(campaign({ status: "in_review", proposal_id: "p1", snapshot: { id: "s1", computed_at: "2026-11-05T17:00:00Z", eligible_count: 2, excluded_counts: {}, hash: "a".repeat(64) } }))
    let revision: unknown = null
    server.use(http.post(`*/api/v1/marketing/campaigns/${CID}/revisions`, async ({ request }) => { revision = await request.json(); return HttpResponse.json({ ...REV, revision_no: 2 }, { status: 201 }) }))
    renderApp(`/marketing/${CID}`)
    expect(await screen.findByText("Waiting for approval")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Open the approval" })).toHaveAttribute("href", "/approvals/p1")
    expect(screen.queryByRole("button", { name: "Submit for approval" })).not.toBeInTheDocument()
    const subject = screen.getByLabelText("Subject")
    await userEvent.clear(subject)
    await userEvent.type(subject, "New subject")
    await userEvent.click(screen.getByRole("button", { name: "Save as new revision" }))
    expect(await screen.findByText("Edit the message?")).toBeInTheDocument()
    expect(revision).toBeNull() // nothing is sent until the person confirms
    await userEvent.click(screen.getByRole("button", { name: "Edit and withdraw" }))
    await waitFor(() => expect(revision).toEqual({ subject: "New subject", body_markdown: REV.body_markdown }))
  })

  it("keeps a sent campaign read-only", async () => {
    mockMe(meWith(...PERMS))
    mockCampaign(campaign({ status: "sent" }))
    renderApp(`/marketing/${CID}`)
    expect((await screen.findAllByText("Sent")).length).toBeGreaterThan(0)
    expect(screen.getByLabelText("Subject")).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Submit for approval" })).not.toBeInTheDocument()
  })

  it("lists suppressed addresses and adds one", async () => {
    mockMe(meWith(...PERMS))
    let added: unknown = null
    server.use(
      http.get("*/api/v1/marketing/campaigns", () => HttpResponse.json([])),
      http.get("*/api/v1/marketing/suppressions", () => HttpResponse.json([{ id: "s1", channel: "email", value_normalized: "ben@example.com", reason: "unsubscribed", created_at: "2026-11-05T17:00:00Z" }])),
      http.post("*/api/v1/marketing/suppressions", async ({ request }) => { added = await request.json(); return HttpResponse.json({ id: "s2", channel: "email", value_normalized: "x@example.com", reason: "manual", created_at: "2026-11-05T17:00:00Z" }, { status: 201 }) }),
    )
    renderApp("/marketing")
    await userEvent.click(await screen.findByRole("tab", { name: "Do not e-mail" }))
    expect(await screen.findByText("ben@example.com")).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Add an e-mail address"), "x@example.com")
    await userEvent.click(screen.getByRole("button", { name: "Add" }))
    await waitFor(() => expect(added).toEqual({ value: "x@example.com", reason: "manual" }))
  })
})


it("explains a failed audience preview, blocks submission and retries successfully", async () => {
  mockMe(meWith(...PERMS))
  mockCampaign(campaign())
  let attempts = 0
  server.use(http.get(`*/api/v1/marketing/segments/${SEG}/preview`, () => {
    attempts++
    return attempts === 1 ? HttpResponse.json({ title: "Audience unavailable", status: 503, code: "unavailable" }, { status: 503 }) : HttpResponse.json(PREVIEW)
  }))
  renderApp(`/marketing/${CID}`)
  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("Audience unavailable")
  expect(screen.getByRole("button", { name: "Submit for approval" })).toBeDisabled()
  expect(screen.getByText("Load the audience preview before submitting.")).toBeInTheDocument()
  await userEvent.click(within(alert).getByRole("button", { name: "Try again" }))
  await screen.findByText("people would receive this")
  expect(screen.getByRole("button", { name: "Submit for approval" })).toBeEnabled()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})
