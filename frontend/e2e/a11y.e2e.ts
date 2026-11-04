// WCAG 2.2 AA gate (09-frontend.md): zero serious or critical axe violations on the main pages.
import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import { storageState } from "./support/accounts.ts"

test.use({ storageState: storageState("morgan") })

const PAGES: [string, string][] = [
  ["/purchasing", "Purchasing"],
  ["/purchasing?tab=quotes", "Purchasing"],
  ["/sales", "Sales"],
  ["/settings/ai", "Settings"],
  ["/today", "Today"], ["/inbox", "Inbox"], ["/approvals", "Approvals"], ["/customers", "Customers"], ["/schedule", "Schedule"], ["/inventory", "Inventory"], ["/marketing", "Marketing"], ["/system", "System"],
]

for (const [path, heading] of PAGES) {
  test(`${heading} (${path}) has no serious accessibility violations`, async ({ page }) => {
    await page.goto(path)
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible()
    await page.waitForLoadState("networkidle")
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze()
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
    expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.html.slice(0, 140)).join(" | ")}`)).toEqual([])
  })
}

test("the public widget has no serious accessibility violations", async ({ browser, baseURL }) => {
  const visitor = await browser.newContext({ baseURL, ignoreHTTPSErrors: true })
  const page = await visitor.newPage()
  await page.goto("/p/example-cycle-works/request")
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([])
  await visitor.close()
})

for (const theme of ["light", "dark"] as const) {
  test(`phone navigation and main pages remain accessible in ${theme} theme`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    for (const [path, heading] of PAGES) {
      await page.goto(path)
      await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible()
      await page.waitForLoadState("networkidle")
      // Without this, axe measures colours halfway through the theme's CSS transitions.
      await page.addStyleTag({ content: "*, *::before, *::after { transition: none !important; animation: none !important; }" })
      await page.locator("html").evaluate((element, dark) => element.classList.toggle("dark", dark), theme === "dark")
      expect(await page.locator("html").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()
      const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
      // On failure, name the elements and the measured contrast so the fix does not need a rerun.
      expect(serious.map((v) => `${v.id}: ${v.nodes.slice(0, 5).map((n) => `${n.target.join(" ")} ${n.any[0]?.message ?? ""}`).join(" | ")}`), path).toEqual([])
    }
    const menu = page.getByRole("button", { name: "Menu", exact: true })
    await menu.focus()
    await page.keyboard.press("Enter")
    const drawer = page.getByRole("dialog", { name: "Main", exact: true })
    await expect(drawer).toBeVisible()
    await expect(drawer.getByRole("link", { name: "Today", exact: true })).toBeVisible()
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([])
    await page.keyboard.press("Escape")
    await expect(drawer).toBeHidden()
    await expect(menu).toBeFocused()
    await menu.click()
    await drawer.getByRole("link", { name: "Today", exact: true }).click()
    await expect(page).toHaveURL(/\/today$/)
    await expect(drawer).toBeHidden()
  })
}

// The live supplier form (rather than only an invalid-token message) is scanned in procurement.e2e.ts.
test("/q/<token> expired-link page has no serious accessibility violations", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, viewport: { width: 390, height: 844 } })
  try {
    const page = await context.newPage()
    await page.goto("/q/a11y.invalid")
    await expect(page.getByRole("alert")).toContainText("expired or is invalid")
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([])
  } finally { await context.close() }
})
