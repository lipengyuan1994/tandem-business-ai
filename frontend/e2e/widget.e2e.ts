/// <reference lib="dom" />
// Reviewer-run only: an actual cross-origin host and Caddy's frame-ancestors policy; no routing mocks.
import { createServer, type Server } from "node:http"
import { expect, test, type Page } from "@playwright/test"
import type { components } from "../src/api/schema.d.ts"
import { storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

type Settings = components["schemas"]["SettingsOut"]
const markup = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic widget host</title><body><h1>Example Cycle Works host</h1><script src="https://localhost:8443/embed.js" data-business="example-cycle-works" async></script></body></html>'

async function host(port: number): Promise<Server> {
  const server = createServer((_request, response) => { response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }); response.end(markup) })
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(port, "localhost", resolve) })
  return server
}
async function close(server: Server) {
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
}
async function openWidget(page: Page) {
  // The public loader deliberately has a closed shadow root. Use its keyboard interface, then the Frame API.
  await expect.poll(() => page.frames().length).toBe(2)
  // Safari's default keyboard setting moves Tab between form fields only; Option+Tab reaches buttons.
  await page.keyboard.press(page.context().browser()?.browserType().name() === "webkit" ? "Alt+Tab" : "Tab")
  await page.keyboard.press("Enter")
}
async function chatFrame(page: Page) {
  await expect.poll(() => page.frames().some((frame) => frame.url() === "https://localhost:8443/embed/example-cycle-works/chat")).toBe(true)
  return page.frames().find((frame) => frame.url() === "https://localhost:8443/embed/example-cycle-works/chat")!
}

test.use({ storageState: storageState("morgan") })

for (const width of [1280, 375]) {
test(`widget at ${width}px: allowed conversation persists, disallowed launcher absent and direct frame refused`, async ({ page, browser }) => {
  const original = await api<Settings>(page.request, "GET", "/api/v1/org/settings")
  const servers: Server[] = []
  const visitor = await browser.newContext({ viewport: { width, height: 812 }, ignoreHTTPSErrors: true, storageState: { cookies: [], origins: [] } })
  try {
    await api(page.request, "PATCH", "/api/v1/org/settings", { version: original.version, settings: { ...original.settings, embed_allowed_origins: ["http://localhost:5555"] } })
    servers.push(await host(5555))
    servers.push(await host(5556))
    const allowed = await visitor.newPage()
    await allowed.goto("http://localhost:5555")
    await openWidget(allowed)
    const chat = await chatFrame(allowed)
    const heading = chat.getByRole("heading", { name: "Message Example Cycle Works" })
    await expect(heading).toBeVisible()
    const iframe = await chat.frameElement()
    await expect(async () => {
      const layout = await iframe.evaluate((element) => {
        const frameElement = element as HTMLIFrameElement
        const frame = frameElement.getBoundingClientRect()
        const header = frameElement.previousElementSibling!.getBoundingClientRect()
        return { top: frame.top, bottom: frame.bottom, left: frame.left, right: frame.right, headerBottom: header.bottom }
      })
      const title = await heading.boundingBox()
      expect(title).not.toBeNull()
      expect(layout.top).toBeGreaterThanOrEqual(layout.headerBottom - 0.5)
      expect(title!.y).toBeGreaterThanOrEqual(layout.top)
      expect(title!.y + title!.height).toBeLessThanOrEqual(layout.bottom)
      expect(layout.left).toBeGreaterThanOrEqual(0)
      expect(layout.right).toBeLessThanOrEqual(width)
      expect(await chat.evaluate(() => scrollY)).toBe(0)
    }).toPass()
    await chat.getByLabel("Your name").fill("Widget Example")
    await chat.getByLabel("Email", { exact: true }).fill("widget.e2e@example.com")
    const text = "Synthetic widget message: what are your opening hours?"
    await chat.getByLabel("Message", { exact: true }).fill(text)
    await chat.getByRole("button", { name: "Send message", exact: true }).click()
    await expect(chat.getByRole("list", { name: "Conversation" }).getByText(text, { exact: true })).toBeVisible()
    expect(await chat.evaluate(() => Boolean(sessionStorage.getItem("smb_embed_example-cycle-works")))).toBe(true)
    await allowed.reload()
    await openWidget(allowed)
    const resumed = await chatFrame(allowed)
    await expect(resumed.getByRole("list", { name: "Conversation" }).getByText(text, { exact: true })).toBeVisible()
    expect(await resumed.evaluate(() => Boolean(sessionStorage.getItem("smb_embed_example-cycle-works")))).toBe(true)
    await expect(resumed.getByLabel("Your name")).toHaveCount(0)

    const refused = await visitor.newPage()
    const violations: string[] = []
    refused.on("console", (message) => { if (/frame-ancestors/i.test(message.text())) violations.push(message.text()) })
    const configRefused = refused.waitForEvent("requestfailed", { predicate: (request) => request.url().endsWith("/api/v1/public/example-cycle-works/embed-config") })
    await refused.goto("http://localhost:5556")
    await configRefused
    // A closed shadow launcher is not accessible to ordinary locators: also assert no host was mounted.
    await expect(refused.locator("body > div")).toHaveCount(0)
    await expect(refused.locator("body > *")).toHaveCount(2) // only the host heading and script
    await expect(refused.getByRole("button")).toHaveCount(0)
    expect(refused.frames()).toHaveLength(1)
    // Bypass the loader with a direct iframe; CSP must still refuse this disallowed ancestor.
    await refused.evaluate(() => {
      const frame = document.createElement("iframe")
      frame.title = "Direct CSP probe"
      frame.src = "https://localhost:8443/embed/example-cycle-works/chat"
      document.body.append(frame)
    })
    await expect.poll(() => violations.some((message) => /violat|refus|block/i.test(message))).toBe(true)
    for (const frame of refused.frames().filter((frame) => frame.parentFrame())) {
      await expect(frame.getByRole("heading", { name: "Message Example Cycle Works" })).toHaveCount(0)
    }
  } finally {
    const current = await api<Settings>(page.request, "GET", "/api/v1/org/settings")
    await api(page.request, "PATCH", "/api/v1/org/settings", { version: current.version, settings: { ...current.settings, embed_allowed_origins: original.settings.embed_allowed_origins } })
    await visitor.close()
    await Promise.all(servers.map(close))
  }
})
}
