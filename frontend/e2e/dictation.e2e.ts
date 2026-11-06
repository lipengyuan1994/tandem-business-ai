import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { expect, test, type Page } from "@playwright/test"

test.use({ launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] } })

const origin = "https://dictation.example.test"
const dist = resolve(import.meta.dirname, "../dist")
const me = {
  user: { id: "00000000-0000-7000-8000-000000000001", display_name: "Morgan Example" }, email: "morgan@example.com",
  memberships: [{ business_id: "00000000-0000-7000-8000-0000000000b1", business_name: "Example Cycle Works", business_slug: "example-cycle-works", roles: ["owner"] }],
  active_business_id: "00000000-0000-7000-8000-0000000000b1", active_business_timezone: "America/Denver",
  permissions: ["approvals.proposal.read", "ai.assistant.use"], csrf_token: "synthetic-csrf", policy_version: "test",
}

async function offlineApp(page: Page, deny = false) {
  const uploads: Buffer[] = []
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) return route.abort()
    const responses: Record<string, unknown> = {
      "/api/v1/auth/me": me,
      "/api/v1/org/settings": { settings: { terminology: {} }, version: 1 },
      "/api/v1/admin/system": { data_profile: "standard" },
      "/api/v1/notifications": { items: [], unread: 0 },
      "/api/v1/dashboard/counters": { approvals_i_can_decide: 0, inbox_waiting_on_staff: 0, unread_notifications: 0 },
      "/api/v1/approvals/proposals": { items: [], next_cursor: null },
      "/api/v1/ai/status": { enabled: true, reason: null, policy: "hosted_vultr", budget_percent: 0, model_classes: { stt: { enabled: true, reason: null } } },
      "/api/v1/ai/copilot/threads": [],
    }
    if (url.pathname === "/api/v1/voice/transcriptions") {
      uploads.push(route.request().postDataBuffer()!)
      return route.fulfill({ json: { text: "Hello there.", raw_text: "um hello there", duration_s: 1, polished: true, warnings: [] } })
    }
    if (Object.hasOwn(responses, url.pathname)) return route.fulfill({ json: responses[url.pathname] })
    if (url.pathname.startsWith("/api/")) throw new Error(`Unexpected offline API request: ${route.request().method()} ${url.pathname}`)
    const asset = url.pathname.startsWith("/assets/") ? url.pathname.slice(1) : "index.html"
    const contentType = asset.endsWith(".js") ? "text/javascript" : asset.endsWith(".css") ? "text/css" : "text/html"
    return route.fulfill({ body: readFileSync(resolve(dist, asset)), contentType, headers: { "Permissions-Policy": deny ? "microphone=()" : "microphone=(self)" } })
  })
  await page.goto(`${origin}/approvals`)
  await page.getByRole("button", { name: "Assistant", exact: true }).click()
  return uploads
}

test("microphone denied by browser policy focuses editable text without uploading", async ({ page }) => {
  const uploads = await offlineApp(page, true)
  const panel = page.getByRole("dialog", { name: "Assistant", exact: true })
  await panel.getByRole("button", { name: "Start dictation" }).click()
  await expect(panel.getByRole("alert")).toHaveText("Microphone permission was denied. You can type your text instead.")
  const input = panel.getByRole("textbox", { name: "Ask the assistant" })
  await expect(input).toBeFocused()
  await input.fill("I can still type")
  await expect(input).toHaveValue("I can still type")
  expect(uploads).toHaveLength(0)
})

test("fake microphone records, inserts text, and Escape cancels without closing the panel", async ({ page }) => {
  const uploads = await offlineApp(page)
  const panel = page.getByRole("dialog", { name: "Assistant", exact: true })
  const input = panel.getByRole("textbox", { name: "Ask the assistant" })
  await input.fill("Before after")
  await input.evaluate((element) => (element as unknown as { setSelectionRange: (start: number, end: number) => void }).setSelectionRange(7, 7))
  await panel.getByRole("button", { name: "Start dictation" }).click()
  await expect(panel.getByText("Recording 0:00")).toBeVisible()
  // Let Chromium emit a nonempty audio chunk; this is a real MediaRecorder with fake device input.
  await expect.poll(() => panel.getByText("Recording 0:01").isVisible()).toBe(true)
  await panel.getByRole("button", { name: "Stop dictation" }).click()
  await expect(input).toHaveValue("Before Hello there.after")
  expect(uploads).toHaveLength(1)
  await panel.getByRole("button", { name: "Start dictation" }).click()
  await expect(panel.getByRole("button", { name: "Stop dictation" })).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(panel).toBeVisible()
  await expect(input).toBeFocused()
  expect(uploads).toHaveLength(1)
})
