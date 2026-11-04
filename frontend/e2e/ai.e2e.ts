// The assistant panel against the dev stack with the scripted `fake` AI provider (no model is called).
// Needs AI_ROUTES_FILE=/config/ai-routes.dev.yaml and AI_FAKE_SCRIPT_FILE in .env (see .env.example).
import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import { storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

test.use({ storageState: storageState("morgan") })

test("assistant: open the panel, ask a question and see a final answer", async ({ page }) => {
  const status = await api<{ enabled: boolean; reason: string | null }>(page.request, "GET", "/api/v1/ai/status")
  test.skip(!status.enabled, `AI is off in this stack (${status.reason ?? "unknown"}): set up the dev fake provider first`)

  await page.goto("/today")
  await page.getByRole("button", { name: "Assistant" }).click()
  const panel = page.getByRole("dialog", { name: "Assistant" })
  await expect(panel.getByText("AI can make mistakes; you approve every change")).toBeVisible()
  await panel.getByLabel("Ask the assistant").fill("What is happening today?")
  await panel.getByRole("button", { name: "Ask" }).click()
  await expect(panel.getByText(/Demo mode/)).toBeVisible() // the scripted copilot answer
  await expect(panel.getByRole("button", { name: "What is happening today?" })).toBeVisible() // the chat was saved
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()
  expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([])

  await page.keyboard.press("Escape")
  await expect(panel).toBeHidden()
  await page.keyboard.press("Control+j")
  await expect(page.getByRole("dialog", { name: "Assistant" })).toBeVisible()
})
