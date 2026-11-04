import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import type { components } from "../src/api/schema.d.ts"
type Schemas = components["schemas"]
import { storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"
import { approveProcurement, procurementApprover, quoteFixture, supplierPath } from "./support/procurement.ts"

test.use({ storageState: storageState("morgan"), trace: "off", screenshot: "off" }) // capability URLs stay out of traces

test("request quotes → supplier public answer → compare → draft purchase order", async ({ page, browser, baseURL }) => {
  test.setTimeout(120_000)
  const fixture = await quoteFixture(page.request)
  const approver = await procurementApprover(browser, page.request, baseURL)
  const supplierContext = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, viewport: { width: 390, height: 844 } })
  try {
    await page.goto("/purchasing?tab=quotes")
    await page.getByRole("button", { name: "Request quotes", exact: true }).click()
    const dialog = page.getByRole("dialog", { name: "Request quotes", exact: true })
    await dialog.getByLabel("Catalog item", { exact: true }).selectOption(fixture.catalog.id)
    await dialog.getByLabel("Quantity", { exact: true }).fill("2")
    await dialog.getByLabel("Needed by", { exact: true }).fill(fixture.body.needed_by)
    await dialog.getByLabel("Reply by", { exact: true }).fill(fixture.body.reply_by)
    await dialog.getByLabel(fixture.supplier.name, { exact: true }).check()
    await dialog.getByLabel("Message (optional)", { exact: true }).fill(fixture.body.message)
    await dialog.getByRole("button", { name: "Request quotes", exact: true }).click()
    const review = dialog.getByRole("link", { name: "Review approval", exact: true })
    await expect(review).toBeVisible()
    const proposalId = (await review.getAttribute("href"))!.split("/").at(-1)!
    const approvalPage = await approver.context.newPage()
    await approveProcurement(approvalPage, proposalId)
    const outbox = await api<Schemas["OutboxOut"][]>(page.request, "GET", `/api/v1/notifications/outbox?proposal_id=${proposalId}`)
    const requestId = outbox.find((o) => o.template_key === "quote_request")?.related_id
    if (!requestId) throw new Error("Approved quote request outbox is missing")
    const me = await api<Schemas["MeOut"]>(page.request, "GET", "/api/v1/auth/me")
    const path = supplierPath(me.active_business_id!, proposalId)

    const supplier = await supplierContext.newPage()
    await supplier.goto(path)
    await expect(supplier.getByRole("heading", { name: "Quote for Example Cycle Works", exact: true })).toBeVisible()
    await supplier.getByLabel(`Unit price (USD) · ${fixture.catalog.name}`, { exact: true }).fill("7.25")
    await supplier.getByLabel(`Quantity available · ${fixture.catalog.name}`, { exact: true }).fill("10")
    await supplier.getByLabel("Lead time (days)", { exact: true }).fill("3")
    const axe = await new AxeBuilder({ page: supplier }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()
    expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([])
    expect(await supplier.locator("html").evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true)
    await supplier.getByRole("button", { name: "Send quote", exact: true }).click()
    await expect(supplier.getByRole("status")).toContainText("Thank you")

    await page.goto(`/purchasing/quote-requests/${requestId}`)
    const comparison = page.getByRole("table", { name: "Quote comparison", exact: true })
    await expect(comparison.getByRole("cell", { name: "$14.50", exact: true })).toBeVisible()
    await expect(comparison.getByRole("columnheader", { name: "Price component", exact: true })).toBeVisible()
    await comparison.getByRole("button", { name: "Create purchase order from this quote", exact: true }).click()
    const poReview = page.getByRole("link", { name: "Review approval", exact: true })
    await expect(poReview).toBeVisible()
    const poProposalId = (await poReview.getAttribute("href"))!.split("/").at(-1)!
    await approveProcurement(approvalPage, poProposalId)
    const detail = await api<Schemas["QuoteRequestOut"]>(page.request, "GET", `/api/v1/purchasing/quote-requests/${requestId}`)
    expect(detail.status).toBe("closed")
    const orders = await api<{ items: Schemas["POOut"][] }>(page.request, "GET", "/api/v1/purchasing/purchase-orders?limit=200")
    const order = orders.items.find((o) => o.source_quote_id === detail.quotes[0]?.id)
    expect(order?.status).toBe("draft")
    expect(order?.total_minor).toBe(1450)
    if (!order) throw new Error("Draft PO missing")
    await page.goto(`/purchasing/purchase-orders/${order.id}`)
    await expect(page.getByRole("heading", { name: "Draft", exact: true })).toBeVisible()
    await expect(page.getByText("$14.50", { exact: true }).first()).toBeVisible()
  } finally {
    await supplierContext.close()
    await approver.restore()
  }
})
