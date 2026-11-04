// The supplier quote comparison (PQ-02) has to fit the 1280 px window a demo video may be recorded in, keep its
// "Create purchase order" button in view and stay accessible. On a phone the table scrolls inside its own labelled
// region, never the page.
import AxeBuilder from "@axe-core/playwright"
import { expect, type Locator, type Page, test } from "@playwright/test"
import { storageState } from "./support/accounts.ts"
import { seededOpenRequest } from "./support/procurement.ts"

test.use({ storageState: storageState("morgan") })

/** Open the seeded brake-pad request (three suppliers, two answers) at a window size; return its comparison. */
async function openComparison(page: Page, width: number, height: number) {
  const request = await seededOpenRequest(page.request)
  await page.setViewportSize({ width, height })
  await page.goto(`/purchasing/quote-requests/${request.id}`)
  const table = page.getByRole("table", { name: "Quote comparison", exact: true })
  const buttons = table.getByRole("button", { name: "Create purchase order from this quote", exact: true })
  await expect(buttons.first()).toBeVisible()
  await page.waitForLoadState("networkidle")
  return { buttons, region: page.locator('section[aria-labelledby="comparison-title"] [data-slot="table-container"]') }
}

/** Every button lies completely inside the table's scroll region (so it is neither cut off nor outside the window). */
async function expectInside(buttons: Locator, region: Locator) {
  const frame = (await region.boundingBox())!
  expect(await buttons.count()).toBeGreaterThan(0)
  for (const button of await buttons.all()) {
    const box = (await button.boundingBox())!
    expect(box.x, "button starts inside the table").toBeGreaterThanOrEqual(frame.x - 0.5)
    expect(box.x + box.width, "button ends inside the table").toBeLessThanOrEqual(frame.x + frame.width + 0.5)
  }
}

async function expectNoSeriousViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze()
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.html.slice(0, 140)).join(" | ")}`)).toEqual([])
}

for (const [width, height] of [[1280, 800], [1440, 900]]) {
  test(`the comparison fits a ${width} px window without sideways scrolling or a cut-off action`, async ({ page }) => {
    const { buttons, region } = await openComparison(page, width!, height!)
    expect(await region.evaluate((e) => e.scrollWidth <= e.clientWidth), "the table does not scroll sideways").toBe(true)
    await expectInside(buttons, region)
    await expectNoSeriousViolations(page)
  })
}

test("on a 375 px phone the table scrolls inside its own region and the action is reachable", async ({ page }) => {
  const { buttons, region } = await openComparison(page, 375, 812)
  expect(await page.locator("html").evaluate((e) => e.scrollWidth <= e.clientWidth), "the page does not scroll sideways").toBe(true)
  await expect(region).toHaveAttribute("tabindex", "0") // keyboard users can scroll it
  await region.evaluate((e) => { e.scrollLeft = e.scrollWidth })
  await expectInside(buttons, region)
  await expectNoSeriousViolations(page)
})
