// Drives the real Keycloak login (password, then first-login password change and TOTP setup, or the OTP prompt).
import { randomBytes } from "node:crypto"
import { type Page, expect } from "@playwright/test"
import { type Account, freshCode } from "./accounts.ts"

const submit = (page: Page) => page.locator('input[type="submit"], button[type="submit"]').first().click()

export async function login(page: Page, account: Account): Promise<void> {
  await page.goto("/api/v1/auth/login?return_to=/today")
  await page.locator("#username").fill(account.email)
  await page.locator("#password").fill(account.password)
  await page.locator("#kc-login").click()
  for (let i = 0; i < 6; i++) {
    await page.waitForLoadState("load")
    if (!new URL(page.url()).pathname.startsWith("/auth/")) return
    if (await page.locator("#password-new").count()) {
      account.password = `E2e-${randomBytes(12).toString("base64url")}`
      account.save(account) // before submitting, so a crash cannot lose the new password
      await page.locator("#password-new").fill(account.password)
      await page.locator("#password-confirm").fill(account.password)
      await submit(page)
    } else if (await page.locator("#totp").count()) {
      if (!(await page.locator("#kc-totp-secret-key").count())) await page.locator("#mode-manual").click()
      account.totpSecret = (await page.locator("#kc-totp-secret-key").innerText()).replace(/\s/g, "")
      account.save(account)
      await page.locator("#totp").fill(await freshCode(account))
      if (await page.locator("#userLabel").count()) await page.locator("#userLabel").fill("e2e")
      await submit(page)
    } else if (await page.locator("#otp").count()) {
      await page.locator("#otp").fill(await freshCode(account))
      await submit(page)
    } else {
      const error = await page.locator("#input-error, .kc-feedback-text, [class*=alert]").first().textContent().catch(() => null)
      throw new Error(`unexpected Keycloak page ${new URL(page.url()).pathname}: ${error ?? (await page.title())}`)
    }
  }
  expect(new URL(page.url()).pathname.startsWith("/auth/"), "still on Keycloak after login").toBe(false)
}
