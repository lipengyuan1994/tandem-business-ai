// Logs in each test person once (TOTP codes are single-use) and saves the session for the tests.
import { expect, test as setup } from "@playwright/test"
import { casey, ellery, morgan, storageState } from "./support/accounts.ts"
import { login } from "./support/login.ts"

setup("Morgan signs in with password and TOTP and picks a business", async ({ page }) => {
  await login(page, morgan())
  await expect(page).toHaveURL(/\/select-business$/) // Morgan belongs to two businesses
  await page.getByRole("button", { name: "Open Example Cycle Works" }).click()
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible()
  await page.context().storageState({ path: storageState("morgan") })
})

setup("Casey completes first login: new password and TOTP enrolment", async ({ page }) => {
  await login(page, casey())
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible()
  const me = await (await page.request.get("/api/v1/auth/me")).json()
  expect(me.memberships.map((m: { roles: string[] }) => m.roles)).toEqual([["operator"]])
  await page.context().storageState({ path: storageState("casey") })
})

setup("Ellery completes first login and opens the seeded dental clinic", async ({ page }) => {
  await login(page, ellery())
  if (new URL(page.url()).pathname === "/select-business") {
    await page.getByRole("button", { name: "Open Example Dental Clinic" }).click()
  }
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible()
  const me = await (await page.request.get("/api/v1/auth/me")).json()
  expect(me.memberships.find((m: { business_slug: string }) => m.business_slug === "example-dental-clinic")?.business_id).toBe(me.active_business_id)
  await page.context().storageState({ path: storageState("ellery") })
})
