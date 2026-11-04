// The demo has no e-mail, so staff hand a pending supplier its own quote page: "Copy link" puts the signed link on the
// clipboard and it opens that supplier's form. The link works like a password: no traces or screenshots, and the test
// never prints it (assertions here compare booleans only).
import { expect, test } from "@playwright/test"
import { storageState } from "./support/accounts.ts"
import { seededOpenRequest } from "./support/procurement.ts"

test.use({ storageState: storageState("morgan"), trace: "off", screenshot: "off" })

test("staff copy the pending supplier's link and it opens that supplier's quote form on a phone", async ({ page, browser, baseURL }) => {
  const request = await seededOpenRequest(page.request)
  expect(request.suppliers.find((s) => s.supplier_name === "Example Parts Supply")?.status).toBe("invited")
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin: baseURL })
  await page.goto(`/purchasing/quote-requests/${request.id}`)
  await page.getByRole("button", { name: "Copy link for Example Parts Supply", exact: true }).click()
  await expect(page.getByText(/^Link copied\./)).toBeVisible()
  const link = String(await page.evaluate("navigator.clipboard.readText()")) // a string: the e2e tsconfig has no DOM types
  expect(/^https:\/\/[^/\s]+\/q\/[\w.-]+$/.test(link), "a signed https quote link is on the clipboard").toBe(true)

  // The supplier opens it, not signed in, in a phone-width window. Reading the form does not answer the request.
  const supplierContext = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, viewport: { width: 390, height: 844 } })
  try {
    const supplier = await supplierContext.newPage()
    await supplier.goto(new URL(link).pathname)
    await expect(supplier.getByRole("heading", { name: "Quote for Example Cycle Works", exact: true })).toBeVisible()
    expect(await supplier.locator("html").evaluate((e) => e.scrollWidth <= e.clientWidth), "the form fits a phone").toBe(true)
  } finally {
    await supplierContext.close()
  }
})
