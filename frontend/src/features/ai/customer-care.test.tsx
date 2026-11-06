import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it, vi } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { TEAM_AGENT, teamItem } from "./team-testing"

const CARE = { ...TEAM_AGENT, key: "customer_care", name: "Customer Care", autonomy: [{ ...TEAM_AGENT.autonomy[0]!, action_type: "customer_care.send_message" }] }
const SETTINGS = { version: 4, settings: { terminology: {}, checkin_delay_days: 2, customer_care_quiet_start_hour: 20, customer_care_quiet_end_hour: 8, customer_care_tone: "warm", public_chat_enabled: true } }
function setup(write = true) {
  mockMe(meWith(...(write ? ["org.settings.write", "org.settings.read", "conversations.proposal.approve"] : [])))
  server.use(http.get("*/api/v1/ai/agents", () => HttpResponse.json({ items: [CARE] })), http.get("*/api/v1/ai/agents/customer_care/activity", () => HttpResponse.json({ items: [teamItem("care", { reasons: ["customer_care.no_consent", "customer_care.dispute", "customer_care.quiet_hours", "customer_care.future_code"] })], next_cursor: null })), http.get("*/api/v1/org/settings", () => HttpResponse.json(SETTINGS)))
}

describe("Customer Care", () => {
  it("explains care reasons and unknown codes in the detail timeline", async () => {
    setup(false)
    renderApp("/ai/team/customer_care")
    for (const reason of ["No consent for reminders", "Open complaint", "Waiting for quiet hours to end", "Customer Care needs a person to check this activity"]) expect(await screen.findByText(reason)).toBeVisible()
  })
  it("saves care settings with the loaded version and preserves unrelated settings", async () => {
    setup()
    let sent: unknown
    server.use(http.patch("*/api/v1/org/settings", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ ...SETTINGS, version: 5 }) }))
    renderApp("/ai/team/customer_care")
    const delay = await screen.findByLabelText("Check-in delay (days)")
    await userEvent.clear(delay)
    await userEvent.type(delay, "0")
    expect(screen.getByText("Check-ins are off")).toBeVisible()
    await userEvent.selectOptions(screen.getByLabelText("Tone"), "formal")
    await userEvent.click(screen.getByRole("button", { name: "Save Customer Care settings" }))
    await waitFor(() => expect(sent).toEqual({ version: 4, settings: { ...SETTINGS.settings, checkin_delay_days: 0, customer_care_tone: "formal" } }))
    expect(screen.getByText(/America\/Denver/)).toBeVisible()
  })
  it("loads a case, identifies unknown customers through Inbox, and resolves the loaded version", async () => {
    setup()
    const careCase = { id: "case1", category: "warranty", priority: "urgent", status: "open", version: 7, customer_id: null, conversation_id: "thread1", source_message_id: "message1", appointment_id: "appointment1", invoice_id: "invoice1", proposal_id: null, resolved_at: null }
    server.use(http.get("*/api/v1/ai/work-queue", () => HttpResponse.json({ items: [{ ...careCase, kind: "customer_care_case", title: "Customer Care: warranty", created_at: "2026-11-06T17:00:00Z" }], next_cursor: null })), http.get("*/api/v1/ai/customer-care/cases/case1", () => HttpResponse.json(careCase)))
    let sent: unknown
    server.use(http.post("*/api/v1/ai/customer-care/cases/case1/resolve", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ ...careCase, status: "resolved", version: 8 }) }))
    renderApp("/ai/work-queue?agent=customer_care")
    await userEvent.click(await screen.findByRole("button", { name: "View case" }))
    expect(await within(await screen.findByRole("region", { name: "Customer Care case details" })).findByRole("link", { name: "Unknown customer — identify in Inbox" })).toHaveAttribute("href", "/inbox/thread1")
    expect(screen.getByRole("region", { name: "Customer Care case details" })).toHaveTextContent("Warranty · Urgent")
    expect(screen.getByRole("link", { name: "Related appointment" })).toHaveAttribute("href", "/schedule?appointment=appointment1")
    await userEvent.click(screen.getByRole("button", { name: "Resolve" }))
    const dialog = await screen.findByRole("dialog", { name: "Resolve Customer Care case?" })
    expect(dialog).toHaveTextContent("lifts this case's hold")
    await userEvent.click(within(dialog).getByRole("button", { name: "Resolve" }))
    await waitFor(() => expect(sent).toEqual({ version: 7 }))
  })
})

it.each([
  ["Check-in delay (days)", "-1"], ["Check-in delay (days)", "366"], ["Check-in delay (days)", "1.5"], ["Check-in delay (days)", ""],
  ["Quiet hours start", "24"], ["Quiet hours end", "-1"], ["Quiet hours end", "2.5"],
])("blocks invalid %s = %s", async (label, value) => {
  setup()
  const patch = vi.fn()
  server.use(http.patch("*/api/v1/org/settings", patch))
  renderApp("/ai/team/customer_care")
  const input = await screen.findByLabelText(label)
  await userEvent.clear(input)
  if (value) await userEvent.type(input, value)
  expect(screen.getByRole("button", { name: "Save Customer Care settings" })).toBeDisabled()
  expect(screen.getByRole("alert")).toHaveTextContent("whole days")
  expect(patch).not.toHaveBeenCalled()
})

it("accepts the backend limits and explains equal quiet hours as off", async () => {
  setup()
  let sent: unknown
  server.use(http.patch("*/api/v1/org/settings", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ ...SETTINGS, version: 5 }) }))
  renderApp("/ai/team/customer_care")
  for (const [label, value] of [["Check-in delay (days)", "365"], ["Quiet hours start", "23"], ["Quiet hours end", "23"]]) {
    const input = await screen.findByLabelText(label!)
    await userEvent.clear(input)
    await userEvent.type(input, value!)
  }
  expect(screen.getByText("Quiet hours are off (start and end are equal)")).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "Save Customer Care settings" }))
  await waitFor(() => expect(sent).toMatchObject({ version: 4, settings: { checkin_delay_days: 365, customer_care_quiet_start_hour: 23, customer_care_quiet_end_hour: 23 } }))
})

it("reloads settings after a 409 and saves with the new version", async () => {
  setup()
  let current = SETTINGS
  const versions: number[] = []
  server.use(http.get("*/api/v1/org/settings", () => HttpResponse.json(current)), http.patch("*/api/v1/org/settings", async ({ request }) => {
    const body = await request.json() as { version: number }
    versions.push(body.version)
    current = { ...SETTINGS, version: 6, settings: { ...SETTINGS.settings, checkin_delay_days: 9 } }
    return versions.length === 1 ? HttpResponse.json({ title: "Conflict", status: 409, code: "org.settings_version_conflict" }, { status: 409 }) : HttpResponse.json({ ...current, version: 7 })
  }))
  renderApp("/ai/team/customer_care")
  await userEvent.click(await screen.findByRole("button", { name: "Save Customer Care settings" }))
  expect(await screen.findByRole("dialog")).toHaveTextContent("This record changed")
  await userEvent.click(screen.getByRole("button", { name: "Reload" }))
  await waitFor(() => expect(screen.getByLabelText("Check-in delay (days)")).toHaveValue(9))
  await userEvent.click(screen.getByRole("button", { name: "Save Customer Care settings" }))
  await waitFor(() => expect(versions).toEqual([4, 6]))
})

it("hides Care settings and switches without write permission", async () => {
  setup(false)
  renderApp("/ai/team/customer_care")
  await screen.findByRole("heading", { name: "Customer Care", level: 1 })
  expect(screen.queryByLabelText("Check-in delay (days)")).not.toBeInTheDocument()
  expect(screen.queryByRole("switch")).not.toBeInTheDocument()
})

it("switches Customer Care with the normal off confirmation and version", async () => {
  setup()
  const patch = vi.fn(async ({ request }: { request: Request }) => {
    const body = await request.json()
    expect(body).toEqual({ enabled: false, version: 3 })
    return HttpResponse.json({ ...CARE, enabled: false, version: 4 })
  })
  server.use(http.patch("*/api/v1/ai/agents/customer_care", patch))
  renderApp("/ai/team")
  await userEvent.click(await screen.findByRole("switch", { name: "Enable Customer Care" }))
  expect(patch).not.toHaveBeenCalled()
  await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Turn off" }))
  await waitFor(() => expect(patch).toHaveBeenCalledTimes(1))
})

const CASE = { id: "case1", category: "complaint", priority: "normal", status: "open", version: 7, customer_id: "customer1", conversation_id: "thread1", source_message_id: "message1", appointment_id: "appointment1", invoice_id: "invoice1", proposal_id: null, resolved_at: null }
function queueCases(write = true) {
  setup(write)
  server.use(http.get("*/api/v1/ai/work-queue", () => HttpResponse.json({ items: [
    { ...CASE, kind: "customer_care_case", title: "Normal complaint", created_at: "2026-11-06T17:00:00Z" },
    { ...CASE, id: "case2", priority: "urgent", customer_id: null, kind: "customer_care_case", title: "Urgent warranty", created_at: "2026-11-05T17:00:00Z" },
    { ...CASE, id: "case3", priority: "high", kind: "customer_care_case", title: "High dispute", created_at: "2026-11-05T18:00:00Z" },
  ], next_cursor: null })), http.get("*/api/v1/ai/customer-care/cases/case1", () => HttpResponse.json(CASE)))
}

it("orders loaded cases by priority and links known and unknown customers", async () => {
  queueCases()
  mockMe(meWith("crm.customer.read"))
  server.use(http.get("*/api/v1/crm/customers/customer1", () => HttpResponse.json({ id: "customer1", display_name: "River Example" })))
  renderApp("/ai/work-queue")
  const table = await screen.findByRole("table")
  await screen.findAllByText("River Example")
  const rows = within(table).getAllByRole("row").slice(1)
  expect(rows.map((row) => row.textContent)).toEqual([expect.stringContaining("Urgent warranty"), expect.stringContaining("High dispute"), expect.stringContaining("Normal complaint")])
  expect(within(rows[2]!).getByRole("link", { name: "River Example" })).toHaveAttribute("href", "/customers/customer1")
  expect(within(rows[0]!).getByRole("link", { name: "Unknown customer — identify in Inbox" })).toHaveAttribute("href", "/inbox/thread1")
})

it("reloads the case after resolve conflict and requires another confirmation", async () => {
  queueCases()
  let current = CASE
  const versions: number[] = []
  server.use(http.get("*/api/v1/ai/customer-care/cases/case1", () => HttpResponse.json(current)), http.post("*/api/v1/ai/customer-care/cases/case1/resolve", async ({ request }) => {
    versions.push((await request.json() as { version: number }).version)
    current = { ...CASE, version: 9 }
    return versions.length === 1 ? HttpResponse.json({ title: "Conflict", status: 409, code: "customer_care.case_version_conflict" }, { status: 409 }) : HttpResponse.json({ ...current, status: "resolved", version: 10 })
  }))
  renderApp("/ai/work-queue")
  const row = (await screen.findByText("Normal complaint")).closest("tr")!
  await userEvent.click(within(row).getByRole("button", { name: "View case" }))
  await userEvent.click(await within(row).findByRole("button", { name: "Resolve" }))
  await userEvent.click(within(await screen.findByRole("dialog", { name: "Resolve Customer Care case?" })).getByRole("button", { name: "Resolve" }))
  await userEvent.click(await screen.findByRole("button", { name: "Reload" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(versions).toEqual([7])
  await userEvent.click(within(row).getByRole("button", { name: "Resolve" }))
  await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Resolve" }))
  await waitFor(() => expect(versions).toEqual([7, 9]))
  await waitFor(() => expect(within(row).queryByRole("button", { name: "Resolve" })).not.toBeInTheDocument())
})

it("does not offer Resolve without conversation approval permission", async () => {
  queueCases(false)
  renderApp("/ai/work-queue")
  const row = (await screen.findByText("Normal complaint")).closest("tr")!
  await userEvent.click(within(row).getByRole("button", { name: "View case" }))
  await within(row).findByRole("region", { name: "Customer Care case details" })
  expect(screen.queryByRole("button", { name: "Resolve" })).not.toBeInTheDocument()
})

it("translates the backend care skip and stop codes without exposing raw codes", async () => {
  setup(false)
  const reasons = {
    duplicate: "Already handled for this customer", contact_preference: "Customer’s contact preference prevents this message",
    customer_erased: "Customer data was erased", customer_inactive: "Customer is inactive or restricted",
    future_appointment: "Customer already has an upcoming appointment", language_unavailable: "Customer’s language needs a person to write the message",
    source_unavailable: "Original conversation or message is unavailable", recall_facts_changed: "Recall details changed; check the current appointment and service",
    facts_changed: "Appointment or service details changed", recall_off: "Recall reminders are off for this service", not_due: "Recall reminder is not due yet",
    minimal_template: "Using minimal wording for this business’s privacy settings", complaint_requires_human: "Needs a person", invoice_unavailable: "Related invoice could not be verified",
  }
  server.use(http.get("*/api/v1/ai/agents/customer_care/activity", () => HttpResponse.json({ items: [teamItem("reasons", { reasons: Object.keys(reasons).map((key) => `customer_care.${key}`) })], next_cursor: null })))
  renderApp("/ai/team/customer_care")
  for (const text of Object.values(reasons)) expect(await screen.findByText(text)).toBeVisible()
  expect(screen.queryByText(/customer_care\./)).not.toBeInTheDocument()
})
