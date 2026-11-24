import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { CONNECTION_MANAGE, SYNC_READ, SYNC_RUN, type Connection, type SyncRecord } from "./api"

// Synthetic data only.
const ROOT = "*/api/v1/accounting/quickbooks"
const empty = { income_account_id: null, receivable_account_id: null, deposit_account_id: null, tax_account_id: null, expense_account_id: null, payable_account_id: null, bank_account_id: null }
const notConnected: Connection = {
  provider: "quickbooks", configured: true, status: "not_connected", environment: "sandbox", business_currency: "USD", auto_sync: true,
  mapping: empty, missing_mapping: [], counts: { synced: 0, failed: 0, skipped: 0 }, version: null,
}
const connected: Connection = {
  ...notConnected, status: "connected", company_name: "Example Books", country: "US", home_currency: "USD", connected_at: "2026-10-28T12:00:00Z",
  sync_from: "2026-10-01", version: 4, mapping: { ...empty, income_account_id: "1", expense_account_id: "3", bank_account_id: "2" },
  last_sync_started_at: "2026-10-28T12:00:00Z", last_sync_finished_at: "2026-10-28T12:01:00Z", counts: { synced: 5, failed: 1, skipped: 0 },
}
const failed: SyncRecord = {
  id: "rec-1", entity_type: "invoice", local_id: "inv-1", label: "Invoice INV-2026-0001", status: "failed", remote_id: null, remote_state: null, attempts: 1,
  last_error: "A business validation error has occurred", last_attempt_at: "2026-10-28T12:01:00Z", next_attempt_at: "2026-10-28T12:06:00Z",
  synced_at: null, updated_at: "2026-10-28T12:01:00Z",
}
const accounts = [
  { id: "1", name: "Sales", account_type: "Income" }, { id: "2", name: "Checking", account_type: "Bank" },
  { id: "3", name: "Supplies", account_type: "Expense" }, { id: "9", name: "Meals", account_type: "Expense" },
]

function mocks(connection: Connection, ...permissions: string[]) {
  mockMe(meWith(SYNC_READ, ...permissions))
  server.use(
    http.get(ROOT, () => HttpResponse.json(connection)),
    http.get(`${ROOT}/accounts`, () => HttpResponse.json(accounts)),
    http.get(`${ROOT}/records`, () => HttpResponse.json({ items: [failed], next_cursor: null })),
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency: "USD", timezone: "America/Denver" })),
  )
}

afterEach(() => { window.history.replaceState(null, "", "/") })

describe("accounting settings", () => {
  it("shows the tab only with accounting access", async () => {
    mockMe(meWith("org.business.read"))
    renderApp("/settings/business")
    expect(await screen.findByRole("link", { name: "Business" })).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Accounting" })).not.toBeInTheDocument()
  })

  it("starts the QuickBooks sign-in for an owner", async () => {
    mocks(notConnected, CONNECTION_MANAGE)
    const assign = vi.fn()
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign, search: "" })
    server.use(http.post(`${ROOT}/connect`, () => HttpResponse.json({ authorize_url: "https://appcenter.intuit.com/connect/oauth2?state=x" })))
    renderApp("/settings/accounting")
    await userEvent.click(await screen.findByRole("button", { name: "Connect QuickBooks" }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://appcenter.intuit.com/connect/oauth2?state=x"))
    vi.restoreAllMocks()
  })

  it("hides connecting from staff who can only read", async () => {
    mocks(notConnected)
    renderApp("/settings/accounting")
    expect(await screen.findByText("Not connected")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Connect QuickBooks" })).not.toBeInTheDocument()
  })

  it("explains a failed connection from the redirect", async () => {
    mocks(notConnected, CONNECTION_MANAGE)
    renderApp("/settings/accounting?quickbooks=error&reason=accounting.oauth_declined")
    expect(await screen.findByRole("alert")).toHaveTextContent("The QuickBooks sign-in was cancelled.")
  })

  it("saves the account mapping with the version", async () => {
    mocks(connected, CONNECTION_MANAGE, SYNC_RUN)
    let body: Record<string, unknown> = {}
    server.use(http.put(`${ROOT}/settings`, async ({ request }) => { body = (await request.json()) as Record<string, unknown>; return HttpResponse.json(connected) }))
    renderApp("/settings/accounting")
    const expense = await screen.findByLabelText("Expense (supplier bills) *")
    await waitFor(() => expect(within(expense).getByRole("option", { name: "Meals" })).toBeInTheDocument())
    expect(within(screen.getByLabelText("Income (sales) *")).queryByRole("option", { name: "Meals" })).not.toBeInTheDocument()
    await userEvent.selectOptions(expense, "9")
    await userEvent.click(screen.getByRole("button", { name: "Save QuickBooks settings" }))
    await waitFor(() => expect(body).toMatchObject({ version: 4, expense_account_id: "9", income_account_id: "1", sync_from: "2026-10-01", auto_sync: true }))
  })

  it("lists failures and retries one", async () => {
    mocks(connected, SYNC_RUN)
    let retried = ""
    server.use(http.post(`${ROOT}/records/:id/retry`, ({ params }) => { retried = String(params.id); return HttpResponse.json({ queued: true }) }))
    renderApp("/settings/accounting")
    expect(await screen.findByText("A business validation error has occurred")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save QuickBooks settings" })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Retry Invoice INV-2026-0001" }))
    await waitFor(() => expect(retried).toBe("rec-1"))
  })

  it("asks before disconnecting", async () => {
    mocks(connected, CONNECTION_MANAGE)
    let version = 0
    server.use(http.post(`${ROOT}/disconnect`, async ({ request }) => { version = ((await request.json()) as { version: number }).version; return HttpResponse.json({ ...connected, status: "disconnected" }) }))
    renderApp("/settings/accounting")
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("Everything already in QuickBooks stays there")
    await userEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }))
    await waitFor(() => expect(version).toBe(4))
  })

  it("keeps checking after Sync now until the run shows up", async () => {
    mocks(connected, SYNC_RUN)
    let posted = 0
    const done = { ...connected, last_sync_started_at: "2026-10-28T12:05:00Z", last_sync_finished_at: "2026-10-28T12:05:30Z", counts: { synced: 9, failed: 0, skipped: 0 } }
    server.use(
      // The worker starts a moment after the request, so the refresh right after the click still shows the old run.
      http.get(ROOT, () => HttpResponse.json(posted && Date.now() - posted > 1000 ? done : connected)),
      http.post(`${ROOT}/sync`, () => { posted = Date.now(); return HttpResponse.json({ queued: true }, { status: 202 }) }),
    )
    renderApp("/settings/accounting")
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }))
    expect(await screen.findByText("9", {}, { timeout: 8000 })).toBeInTheDocument()
  }, 12_000)
})
