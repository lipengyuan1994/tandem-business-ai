// Reviewer-run only: synchronous emergency hand-over is deterministic and needs no model/provider.
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { expect, test } from "@playwright/test"
import type { components } from "../src/api/schema.d.ts"
import { REPO_ROOT, storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

test.use({ storageState: storageState("ellery") })

test("dental: public banner and emergency instructions; emergency gets a system reply and human hand-over", async ({ page, browser, baseURL }) => {
  const fixtures = JSON.parse(readFileSync(resolve(REPO_ROOT, "data/fixtures.json"), "utf8")) as { tables: { businesses: { slug: string; settings_json?: string }[] } }
  const clinic = fixtures.tables.businesses.find((b) => b.slug === "example-dental-clinic")!
  const instructions = (JSON.parse(clinic.settings_json!) as { emergency_instructions: string }).emergency_instructions
  const visitor = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, storageState: { cookies: [], origins: [] } })
  try {
    const chat = await visitor.newPage()
    await chat.goto("/p/example-dental-clinic/chat")
    await expect(chat.getByRole("note").filter({ hasText: "Synthetic demo data." })).toContainText("models hosted locally under the clinic's control")
    await expect(chat.getByRole("note").filter({ hasText: instructions })).toBeVisible()
    await chat.getByLabel("Your name").fill("Dental Visitor Example")
    await chat.getByLabel("Email", { exact: true }).fill("dental.visitor.e2e@example.com")
    const message = "severe tooth pain and my face is swelling"
    await chat.getByLabel("Message", { exact: true }).fill(message)
    const reply = chat.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/v1/public/example-dental-clinic/chat"))
    await chat.getByRole("button", { name: "Send message", exact: true }).click()
    const response = await reply
    expect(response.ok()).toBe(true)
    const publicThread = await response.json() as components["schemas"]["PublicThreadOut"]
    const deterministic = `A person must handle this message. ${instructions} If this is life-threatening call your local emergency number.`
    const outbound = publicThread.messages.filter((m) => m.from_business)
    expect(outbound).toHaveLength(1)
    expect(outbound[0]).toMatchObject({ is_system: true, body: deterministic })
    await expect(chat.getByRole("list", { name: "Conversation" }).getByText(deterministic, { exact: true })).toBeVisible()

    const thread = await api<components["schemas"]["ThreadOut"]>(page.request, "GET", `/api/v1/conversations/${publicThread.conversation_id}`)
    expect(thread).toMatchObject({ emergency: true, ai_mode: "human", status: "waiting_on_staff" })
    expect(thread.handoff_requested_at).toBeTruthy()
    expect(thread.messages.filter((m) => m.direction === "outbound")).toEqual([expect.objectContaining({ author_kind: "system", body: deterministic })])
    expect(thread.messages.some((m) => m.author_kind === "assistant")).toBe(false)
    await page.goto(`/inbox/${publicThread.conversation_id}`)
    await expect(page.getByText(message, { exact: true })).toBeVisible()
    await expect(page.getByText(deterministic, { exact: true })).toBeVisible()
  } finally {
    await visitor.close()
  }
})
