// Local synthetic-stack fixtures. Tokens stay in memory; they are never logged or written to a report.
import { execFileSync } from "node:child_process"
import { expect, type APIRequestContext, type Browser, type Page } from "@playwright/test"
import type { components } from "../../src/api/schema.d.ts"
type Schemas = components["schemas"]
import { REPO_ROOT, storageState } from "./accounts.ts"
import { api } from "./api.ts"

/** Temporarily give Casey approval rights, restoring the seeded roles even on failure. */
export async function procurementApprover(browser: Browser, owner: APIRequestContext, baseURL?: string) {
  const members = await api<Schemas["MemberOut"][]>(owner, "GET", "/api/v1/identity/members")
  const casey = members.find((m) => m.email === "casey@example.com")
  if (!casey) throw new Error("Seeded Casey is missing")
  const path = `/api/v1/identity/members/${casey.membership_id}/roles`
  await api(owner, "PUT", path, { roles: [...new Set([...casey.roles, "admin"])] })
  try {
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, storageState: storageState("casey") })
    return { context, restore: async () => { try { await api(owner, "PUT", path, { roles: casey.roles }) } finally { await context.close() } } }
  } catch (error) {
    await api(owner, "PUT", path, { roles: casey.roles })
    throw error
  }
}
export async function approveProcurement(page: Page, proposalId: string) {
  await page.goto(`/approvals/${proposalId}`)
  await page.getByRole("button", { name: /^Approve:/ }).click()
  await expect(page.getByText("Succeeded", { exact: true }).first()).toBeVisible({ timeout: 30_000 })
}

/** Read the real outbox invitation through the local container, since the public API hides capabilities. */
export function supplierPath(businessId: string, proposalId: string): string {
  const script = `
import sys
from uuid import UUID
from sqlalchemy import text
from app.platform.context import system_context
from app.platform.db import transaction
with transaction(system_context(UUID(sys.argv[1]))) as db:
    url = db.scalar(text("select variables->>'quote_url' from outbox_messages where proposal_id=cast(:id as uuid) and template_key='quote_request' order by id limit 1"), {"id": sys.argv[2]})
    if not url:
        raise RuntimeError("Synthetic quote invitation is missing")
    print('/q/' + url.rsplit('/q/', 1)[1])
`
  const file = process.env.E2E_COMPOSE_FILE ?? "deploy/compose/compose.dev.yml"
  const envFile = process.env.E2E_ENV_FILE
  // Same project selection as global setup; stdout is captured and never echoed.
  const args = ["compose", "-f", file, ...(envFile ? ["--env-file", envFile] : []), "exec", "-T", "api", "python", "-c", script, businessId, proposalId]
  return execFileSync("docker", args, { cwd: REPO_ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim()
}

export async function quoteFixture(owner: APIRequestContext) {
  const suppliers = await api<{ items: Schemas["SupplierOut"][] }>(owner, "GET", "/api/v1/purchasing/suppliers?limit=200")
  const supplier = suppliers.items.find((s) => s.email && s.items?.length)
  if (!supplier) throw new Error("Seeded supplier items are missing")
  const item = supplier.items![0]!
  const items = await api<{ items: Schemas["CatalogItemOut"][] }>(owner, "GET", "/api/v1/catalog/items?kind=product&limit=200")
  const catalog = items.items.find((i) => i.id === item.catalog_item_id)
  if (!catalog) throw new Error("Seeded supplier catalog item is missing")
  const day = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10)
  const body = { lines: [{ item_id: item.catalog_item_id, quantity: "2" }], supplier_ids: [supplier.id], needed_by: day(14), reply_by: day(7), message: "Synthetic procurement e2e" }
  return { supplier, catalog, body }
}

/** The seeded open brake-pad request: three suppliers invited, two answered (Example Parts Supply has not). */
export async function seededOpenRequest(owner: APIRequestContext) {
  const list = await api<{ items: Schemas["QuoteRequestOut"][] }>(owner, "GET", "/api/v1/purchasing/quote-requests?status=sent&limit=200")
  const request = list.items.find((r) => r.suppliers.length === 3 && r.quotes.length >= 2 && r.lines.some((l) => l.name === "Brake pads (pair)"))
  if (!request) throw new Error("The seeded open quote request is missing: run make seed reset=1")
  return request
}
