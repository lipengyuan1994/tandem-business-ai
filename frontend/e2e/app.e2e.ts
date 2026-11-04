// Conflict handling and business isolation in the real stack.
import { expect, test } from "@playwright/test"
import { storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

test.use({ storageState: storageState("morgan") })

interface CustomerPage { items: { id: string; display_name: string; version: number }[] }

test("editing a customer someone else just changed shows the conflict dialog", async ({ page }) => {
  const { items } = await api<CustomerPage>(page.request, "GET", "/api/v1/crm/customers?q=Ben")
  const ben = items.find((c) => c.display_name.startsWith("Ben"))!
  await page.goto(`/customers/${ben.id}`)
  await page.getByRole("button", { name: "Edit customer" }).click()
  await page.getByLabel("Name").fill("Ben Example (front desk)")
  // Meanwhile a colleague saves a change.
  const current = await api<{ version: number }>(page.request, "GET", `/api/v1/crm/customers/${ben.id}`)
  await api(page.request, "PATCH", `/api/v1/crm/customers/${ben.id}`, { version: current.version, display_name: "Ben Example (colleague)" })
  await page.getByRole("button", { name: "Save" }).click()
  await expect(page.getByRole("heading", { name: "This record changed since you opened it" })).toBeVisible()
  await expect(page.getByLabel("Name")).toHaveValue("Ben Example (front desk)") // nothing typed is lost
})

test("switching business never shows the other business's data", async ({ page }) => {
  const cycle = await api<CustomerPage>(page.request, "GET", "/api/v1/crm/customers?q=Ada")
  const ada = cycle.items[0]!
  try {
    await page.goto("/today")
    await page.getByRole("button", { name: "Account menu" }).click()
    await page.getByRole("menuitem", { name: "Switch business" }).click()
    await page.getByRole("button", { name: "Open Example Garden Store" }).click()
    await expect(page.getByRole("heading", { name: "Today" })).toBeVisible()
    await page.goto("/customers")
    await expect(page.getByText("Gwen Example")).toBeVisible()
    await expect(page.getByText("Ada Example")).toHaveCount(0)
    await page.goto(`/customers/${ada.id}`)
    await expect(page.getByText("Customer not found")).toBeVisible()
    expect((await page.request.get(`/api/v1/crm/customers/${ada.id}`)).status()).toBe(404)
  } finally {
    await page.goto("/select-business")
    await page.getByRole("button", { name: "Open Example Cycle Works" }).click()
    await expect(page.getByRole("heading", { name: "Today" })).toBeVisible()
    // Switching business rotates the session (fixation protection); keep the new one for the next tests.
    await page.context().storageState({ path: storageState("morgan") })
  }
})
