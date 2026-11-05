import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { AudiencePreview, CampaignDetail } from "@/api/types"
import { mockStatus, ON, RUN } from "@/features/ai/testing"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const CID = "00000000-0000-7000-8000-0000000000c9"
const SEG = "00000000-0000-7000-8000-0000000000e9"
const PERMS = ["marketing.campaign.read", "marketing.campaign.write", "catalog.item.read"]
const AI_PERMS = [...PERMS, "ai.assistant.use"]
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

describe("campaign AI draft", () => {
  it.each(["ai.assistant.use", "marketing.campaign.write"])("hides drafting without %s", async (permission) => {
    mockMe(meWith(...AI_PERMS.filter((p) => p !== permission)))
    mockStatus()
    mockCampaign(campaign())
    const { queryClient } = renderApp(`/marketing/${CID}`)
    await screen.findByLabelText("Subject")
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(screen.queryByRole("button", { name: "Draft with AI" })).not.toBeInTheDocument()
  })

  it.each(["ai.not_configured", "ai.disabled_by_policy", "ai.budget_exceeded"])("hides drafting when AI is unavailable: %s", async (reason) => {
    mockMe(meWith(...AI_PERMS))
    mockStatus({ ...ON, enabled: false, reason })
    mockCampaign(campaign())
    const { queryClient } = renderApp(`/marketing/${CID}`)
    await screen.findByLabelText("Subject")
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(screen.queryByRole("button", { name: "Draft with AI" })).not.toBeInTheDocument()
  })

  it.each(["approved", "sending", "sent", "cancelled"] as const)("hides drafting for a %s campaign", async (status) => {
    mockMe(meWith(...AI_PERMS))
    mockStatus()
    mockCampaign(campaign({ status }))
    const { queryClient } = renderApp(`/marketing/${CID}`)
    expect(await screen.findByLabelText("Subject")).toBeDisabled()
    await waitFor(() => expect(queryClient.isFetching()).toBe(0))
    expect(screen.queryByRole("button", { name: "Draft with AI" })).not.toBeInTheDocument()
  })

  it("creates and reloads the saved assistant revision, guarding edits while pending", async () => {
    mockMe(meWith(...AI_PERMS))
    mockStatus()
    let saved = campaign({ purpose: "Invite riders for a service" })
    mockCampaign(saved)
    let requestBody: unknown
    let csrf: string | null = null
    let posts = 0
    let release!: () => void
    const pending = new Promise<void>((resolve) => { release = resolve })
    const revision = { ...REV, id: "r2", revision_no: 2, subject: "Keep your bike ready", body_markdown: "Visit us for a friendly service.", created_by_kind: "assistant" }
    server.use(
      http.get(`*/api/v1/marketing/campaigns/${CID}`, () => HttpResponse.json(saved)),
      http.post(`*/api/v1/ai/campaigns/${CID}/draft`, async ({ request }) => {
        posts++
        requestBody = await request.json()
        csrf = request.headers.get("X-CSRF-Token")
        await pending
        saved = campaign({ current_revision: revision, revisions: [REV, revision] })
        return HttpResponse.json({ run_id: RUN, revision_id: revision.id })
      }),
    )
    renderApp(`/marketing/${CID}`)
    const button = await screen.findByRole("button", { name: "Draft with AI" })
    expect(button).not.toHaveAttribute("aria-haspopup")
    expect(screen.getByLabelText("What is this campaign for? (optional)")).toHaveValue("Invite riders for a service")
    await userEvent.click(button)
    await waitFor(() => expect(posts).toBe(1))
    expect(screen.getByRole("button", { name: "Drafting…" })).toBeDisabled()
    expect(screen.getByLabelText("Subject")).toBeDisabled()
    expect(screen.getByLabelText("Message")).toBeDisabled()
    expect(screen.getByLabelText("What is this campaign for? (optional)")).toBeDisabled()
    expect(screen.getByRole("button", { name: "Submit for approval" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Cancel campaign" })).toBeDisabled()
    release()
    await waitFor(() => expect(screen.getByLabelText("Subject")).toHaveValue(revision.subject))
    expect(screen.getByLabelText("Message")).toHaveValue(revision.body_markdown)
    expect(await screen.findByText(/AI draft saved/)).toHaveAttribute("role", "status")
    expect(screen.getByText(/Revision 2 by the assistant/)).toHaveTextContent(revision.subject)
    expect(requestBody).toEqual({ purpose: "Invite riders for a service" })
    expect(csrf).toBe("csrf-test-token")
    expect(posts).toBe(1)
    expect(await screen.findByRole("button", { name: "Draft with AI" })).toBeEnabled()
  })

  it.each(["Subject", "Message"])("preserves unsaved %s edits and explains why drafting is disabled", async (label) => {
    mockMe(meWith(...AI_PERMS))
    mockStatus()
    mockCampaign(campaign())
    let posts = 0
    server.use(http.post(`*/api/v1/ai/campaigns/${CID}/draft`, () => { posts++; return HttpResponse.json({ run_id: RUN, revision_id: "r2" }) }))
    renderApp(`/marketing/${CID}`)
    const button = await screen.findByRole("button", { name: "Draft with AI" })
    const field = screen.getByLabelText(label)
    await userEvent.type(field, " unsaved")
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription("Save your message edits before drafting with AI.")
    await userEvent.click(button)
    expect(posts).toBe(0)
    expect(field).toHaveValue(`${label === "Subject" ? REV.subject : REV.body_markdown} unsaved`)
    await userEvent.clear(field)
    await userEvent.type(field, label === "Subject" ? REV.subject : REV.body_markdown)
    expect(button).toBeEnabled()
  })

  it("confirms drafting in review before withdrawing the approval and restores focus", async () => {
    mockMe(meWith(...AI_PERMS))
    mockStatus()
    mockCampaign(campaign({ status: "in_review", proposal_id: "p1" }))
    let posts = 0
    server.use(http.post(`*/api/v1/ai/campaigns/${CID}/draft`, () => { posts++; return HttpResponse.json({ run_id: RUN, revision_id: "r2" }) }))
    renderApp(`/marketing/${CID}`)
    const button = await screen.findByRole("button", { name: "Draft with AI" })
    await userEvent.click(button)
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/The approval request will be withdrawn/)).toBeInTheDocument()
    expect(posts).toBe(0)
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))
    await waitFor(() => expect(button).toHaveFocus())
    expect(posts).toBe(0)
    await userEvent.click(button)
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Edit and withdraw" }))
    await waitFor(() => expect(posts).toBe(1))
  })

  it.each([
    [429, "ai.budget_exceeded", "Today's AI limit has been used up."],
    [503, "ai.provider_unavailable", "The AI service did not answer."],
    [409, "ai.not_configured", "AI is not set up on this installation yet."],
    [409, "ai.disabled_by_policy", "AI is turned off by your privacy settings."],
    [502, "ai.invalid_model_output", "The AI could not produce a usable draft that passes safety checks."],
  ])("explains %s %s without losing the saved message", async (status, code, message) => {
    mockMe(meWith(...AI_PERMS))
    mockStatus()
    mockCampaign(campaign())
    server.use(http.post(`*/api/v1/ai/campaigns/${CID}/draft`, () => HttpResponse.json({ status, code, title: "AI request failed" }, { status: Number(status) })))
    renderApp(`/marketing/${CID}`)
    await userEvent.click(await screen.findByRole("button", { name: "Draft with AI" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(String(message))
    expect(screen.getByLabelText("Subject")).toHaveValue(REV.subject)
    expect(screen.getByLabelText("Message")).toHaveValue(REV.body_markdown)
    expect(screen.getByRole("button", { name: "Draft with AI" })).toBeEnabled()
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
