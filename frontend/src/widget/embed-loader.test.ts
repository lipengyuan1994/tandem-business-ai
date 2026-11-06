import source from "../../public/embed.js?raw"
import { fireEvent, waitFor, within } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { afterEach, expect, it, vi } from "vitest"
import { server } from "@/test/server"

afterEach(() => { vi.restoreAllMocks(); document.body.replaceChildren() })

it("mounts only owned elements, uses the business label, and enforces frame origin/source on messages", async () => {
  const script = document.createElement("script")
  script.src = "https://desk.example/embed.js"; script.dataset.business = "example-cycle-works"
  const before = document.createElement("p"); before.textContent = "Shop content"; document.body.append(before)
  vi.spyOn(document, "currentScript", "get").mockReturnValue(script)
  const attach = Element.prototype.attachShadow
  vi.spyOn(Element.prototype, "attachShadow").mockImplementation(function (this: Element) { return attach.call(this, { mode: "open" }) })
  server.use(http.get("https://desk.example/api/v1/public/example-cycle-works/embed-config", () => HttpResponse.json({ label: "Ask Example", allowed_origins: [location.origin] })))
  // Execute the shipped script, not a rewritten component.
  window.eval(source)
  const host = document.body.lastElementChild!
  const shadow = host.shadowRoot!
  const own = within(shadow as unknown as HTMLElement)
  const open = await own.findByRole("button", { name: "Ask Example" })
  expect(before.textContent).toBe("Shop content")
  const panel = shadow.querySelector<HTMLElement>("section")!
  const frame = shadow.querySelector<HTMLIFrameElement>("iframe")!
  const frameWindow = { postMessage: vi.fn() }
  Object.defineProperty(frame, "contentWindow", { value: frameWindow })
  fireEvent.click(open)
  expect(panel.hidden).toBe(false); expect(open).toHaveAttribute("aria-expanded", "true")
  fireEvent.load(frame)
  expect(frameWindow.postMessage).toHaveBeenCalledWith({ type: "smb:open" }, "https://desk.example")
  fireEvent(window, new MessageEvent("message", { origin: "https://evil.example", source: frameWindow as unknown as Window, data: { type: "smb:close" } }))
  expect(panel.hidden).toBe(false)
  fireEvent(window, new MessageEvent("message", { origin: "https://desk.example", source: window, data: { type: "smb:close" } }))
  expect(panel.hidden).toBe(false)
  fireEvent(window, new MessageEvent("message", { origin: "https://desk.example", source: frameWindow as unknown as Window, data: { type: "smb:resize", height: 300 } }))
  expect(panel.style.height).toContain("300px")
  fireEvent(window, new MessageEvent("message", { origin: "https://desk.example", source: frameWindow as unknown as Window, data: { type: "smb:close" } }))
  expect(panel.hidden).toBe(true); expect(shadow.activeElement).toBe(open)
  fireEvent.click(open); fireEvent.keyDown(shadow, { key: "Escape" })
  await waitFor(() => expect(panel.hidden).toBe(true))
})
