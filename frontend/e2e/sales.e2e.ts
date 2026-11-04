// Uses the shared global demo reset, configured baseURL and Morgan's owner login, like marketing.e2e.ts.
import { expect, test } from "@playwright/test"
import { formatMoney } from "../src/lib/money.ts"
import { storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

test.use({ storageState: storageState("morgan") })

interface StockLevel { item_id: string; item_name: string; available: string }
interface Invoice {
  number: string
  status: string
  bill_to_name: string
  currency: string
  subtotal_minor: number
  tax_minor: number
  total_minor: number
}

test("quote → fulfilled order → issued invoice, printable totals and invoice list", async ({ page }) => {
  await page.goto("/sales")
  await expect(page.getByRole("heading", { name: "Sales", level: 1 })).toBeVisible()

  // The flagship e2e also consumes pads. Replenish only the synthetic shortage for this two-pair order.
  const levels = await api<StockLevel[]>(page.request, "GET", "/api/v1/inventory/levels")
  const pads = levels.find((item) => item.item_name === "Brake pads (pair)")
  if (!pads) throw new Error("Seeded Brake pads (pair) stock is missing")
  const available = Number(pads.available)
  expect(Number.isFinite(available), "brake pad availability is numeric").toBe(true)
  if (available < 2) {
    await api(page.request, "POST", "/api/v1/inventory/movements", {
      item_id: pads.item_id, quantity: String(2 - available), reason: "receipt",
      note: "Synthetic stock for the sales e2e two-pair order",
    })
  }

  await page.getByRole("button", { name: "New quote", exact: true }).click()
  const create = page.getByRole("dialog", { name: "New quote", exact: true })
  await create.getByLabel("Customer", { exact: true }).fill("Ada Example")
  await create.getByRole("button", { name: "Ada Example", exact: true }).click()
  await create.getByLabel("Catalog item", { exact: true }).selectOption({ label: "Brake pads (pair)" })
  await create.getByLabel("Quantity", { exact: true }).fill("2")
  await expect(create.getByLabel(/^Unit price\b/i)).toHaveValue("25.00")
  await create.getByRole("button", { name: "Create draft", exact: true }).click()

  await expect(create).toBeHidden()
  await expect(page).toHaveURL(/\/sales\/quotes\/[^/]+$/)
  const printable = page.getByRole("article", { name: "Printable document", exact: true })
  await expect(printable.getByRole("heading", { name: "Example Cycle Works", exact: true })).toBeVisible()
  const line = printable.getByRole("row").filter({ has: page.getByRole("cell", { name: "Brake pads (pair)", exact: true }) })
  await expect(line.getByRole("cell", { name: /^2(?:\.0+)?$/ })).toBeVisible()

  await page.getByRole("button", { name: "Send quote", exact: true }).click()
  await expect(printable.getByText("Sent", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Accept quote", exact: true }).click()
  await expect(printable.getByText("Accepted", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Convert to order", exact: true }).click()
  await expect(page).toHaveURL(/\/sales\/orders\/[^/]+$/)

  await page.getByRole("button", { name: "Confirm order", exact: true }).click()
  await expect(printable.getByText("Confirmed", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Fulfil order", exact: true }).click()
  await expect(printable.getByText("Fulfilled", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Create invoice", exact: true }).click()
  await expect(page).toHaveURL(/\/sales\/invoices\/[^/]+$/)

  await page.getByRole("button", { name: "Issue invoice", exact: true }).click()
  const issue = page.getByRole("dialog", { name: "Issue this invoice?", exact: true })
  await expect(issue).toBeVisible()
  await issue.getByRole("button", { name: "Issue invoice", exact: true }).click()
  await expect(issue).toBeHidden()
  const invoiceHeading = page.getByRole("heading", { name: /^INV-\d{4}-\d{4}$/, level: 1 })
  await expect(invoiceHeading).toBeVisible()
  await expect(printable.getByText("Issued", { exact: true })).toBeVisible()
  const number = await invoiceHeading.innerText()

  // Compare rendered amounts with the saved invoice; do not assume the configured default tax.
  const invoice = await api<Invoice>(page.request, "GET", `/api/v1${new URL(page.url()).pathname}`)
  expect(invoice).toMatchObject({ number, status: "issued", bill_to_name: "Ada Example", currency: "USD", subtotal_minor: 5000 })
  const money = (amount: number) => formatMoney({ amount_minor: amount, currency: invoice.currency })
  const total = money(invoice.total_minor)
  for (const name of ["Subtotal", "Tax", "Total"]) {
    await expect(printable.getByRole("term").filter({ hasText: new RegExp(`^${name}$`) })).toBeVisible()
  }
  await expect(printable.getByRole("definition")).toContainText([money(invoice.subtotal_minor), money(invoice.tax_minor), total])

  // Print uses window.print(), not a popup route. Intercept the native dialog, then inspect real print CSS.
  const printState = await page.evaluateHandle(() => {
    const state = { requested: false }
    const browser = globalThis as typeof globalThis & { print: () => void }
    browser.print = () => { state.requested = true }
    return state
  })
  await page.getByRole("button", { name: "Print", exact: true }).click()
  await expect.poll(() => printState.evaluate((state) => state.requested)).toBe(true)
  await page.emulateMedia({ media: "print" })
  try {
    await expect(printable.getByRole("region", { name: "Bill to", exact: true }).getByText("Ada Example", { exact: true })).toBeVisible()
    await expect(printable.getByRole("definition").last()).toHaveText(total)
    await expect(printable.getByRole("definition").last()).toBeVisible()
    await expect(page.getByRole("navigation", { name: "Main", exact: true })).toBeHidden()
  } finally {
    await page.emulateMedia({ media: "screen" })
    await printState.dispose()
  }

  await page.getByRole("link", { name: "Back to sales", exact: true }).click()
  await page.getByRole("tab", { name: "Invoices", exact: true }).click()
  const invoiceRow = page.getByRole("table", { name: "Invoices", exact: true }).getByRole("row")
    .filter({ has: page.getByRole("link", { name: number, exact: true }) })
  await expect(invoiceRow.getByRole("cell", { name: "Ada Example", exact: true })).toBeVisible()
  await expect(invoiceRow.getByRole("cell", { name: "Issued", exact: true })).toBeVisible()
  await expect(invoiceRow.getByRole("cell", { name: total, exact: true })).toBeVisible()
})
