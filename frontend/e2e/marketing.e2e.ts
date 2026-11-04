// Marketing in the real stack: build an audience from filters, see who is excluded and why, freeze it and ask for approval.
import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import { storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

test.use({ storageState: storageState("morgan") })

test("campaign: audience preview with exclusion reasons, then submit for approval", async ({ page }) => {
  await page.goto("/marketing")
  await page.getByRole("button", { name: "New campaign" }).click()
  const dialog = page.getByRole("dialog")
  await dialog.getByLabel("Campaign name").fill("Road bike service offer")
  await dialog.getByLabel("Add a filter").selectOption("tag")
  await dialog.getByLabel("Value").fill("road-bike")
  await dialog.getByRole("button", { name: "Add filter" }).click()
  await dialog.getByLabel("Subject").fill("Time for your spring service")
  await dialog.getByLabel("Message").fill("Book a tune-up this month and get 10% off.")
  await dialog.getByRole("button", { name: "Create draft" }).click()

  await expect(page.getByRole("heading", { name: "Road bike service offer", level: 1 })).toBeVisible()
  await expect(page.getByText(/would receive this/)).toBeVisible()
  // The seeded demo has customers with the tag who never agreed to marketing: they must be listed as excluded, not mailed.
  await expect(page.getByRole("list", { name: "Excluded" })).toBeVisible()
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()
  expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([])

  await page.getByRole("button", { name: "Submit for approval" }).click()
  await expect(page.getByText("Waiting for approval")).toBeVisible()
  await expect(page.getByText(/Audience frozen at submission/)).toBeVisible()
  const link = page.getByRole("link", { name: "Open the approval" })
  await link.click()
  await expect(page.getByRole("heading", { name: "Send campaign: Road bike service offer" })).toBeVisible()
  await expect(page.getByText("E-mail (simulated)").first()).toBeVisible() // what approving would do, before anything is sent
  // Morgan created it, so the approval button is not offered (no self-approval): the proposal waits for another person.
  const proposals = await api<{ items: { title: string; status: string }[] }>(page.request, "GET", "/api/v1/approvals/proposals?status=pending_review&limit=50")
  expect(proposals.items.some((p) => p.title === "Send campaign: Road bike service offer")).toBe(true)
})
