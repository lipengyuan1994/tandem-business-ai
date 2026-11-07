import source from "../../public/embed.js?raw"
import { fireEvent, waitFor, within } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { afterEach, expect, it, vi } from "vitest"
import { server } from "@/test/server"

afterEach(() => { vi.restoreAllMocks(); document.body.replaceChildren(); document.head.querySelectorAll("[data-loader-layout]").forEach((node) => node.remove()) })

function install() {
  const script = document.createElement("script")
  script.src = "https://desk.example/embed.js"; script.dataset.business = "example-cycle-works"
  const before = document.createElement("p"); before.textContent = "Shop content"; document.body.append(before)
  vi.spyOn(document, "currentScript", "get").mockReturnValue(script)
  const attach = Element.prototype.attachShadow
  const roots: ShadowRoot[] = []
  const spy = vi.spyOn(Element.prototype, "attachShadow").mockImplementation(function (this: Element, init: ShadowRootInit) {
    const root = attach.call(this, init); roots.push(root); return root
  })
  window.eval(source)
  return { roots, spy, before }
}

it("mounts only owned elements, uses the business label, and enforces frame origin/source on messages", async () => {
  server.use(http.get("https://desk.example/api/v1/public/example-cycle-works/embed-config", () => HttpResponse.json({ label: "Ask Example", allowed_origins: [location.origin] })))
  // Execute the shipped script, not a rewritten component.
  const { roots, before } = install()
  await waitFor(() => expect(roots).toHaveLength(1))
  const shadow = roots[0]!
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

it("mounts nothing while config is pending, then mounts a closed root with the validated label", async () => {
  let complete!: (response: Response) => void
  vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise<Response>((resolve) => { complete = resolve }))
  const { roots, spy, before } = install()
  expect(spy).not.toHaveBeenCalled()
  expect(document.body.children).toHaveLength(1)
  complete(new Response(JSON.stringify({ label: "Ask Example" }), { status: 200 }))
  await waitFor(() => expect(roots).toHaveLength(1))
  expect(spy).toHaveBeenCalledWith({ mode: "closed" })
  expect(document.body.lastElementChild!.shadowRoot).toBeNull()
  expect(within(roots[0]! as unknown as HTMLElement).getByRole("button", { name: "Ask Example" })).toBeInTheDocument()
  expect(before.textContent).toBe("Shop content")
})

it.each([
  ["CORS/network rejection", () => Promise.reject(new TypeError("Failed to fetch"))],
  ["HTTP refusal", () => Promise.resolve(new Response("", { status: 403 }))],
  ["HTTP server failure", () => Promise.resolve(new Response("", { status: 500 }))],
  ["invalid JSON", () => Promise.resolve(new Response("not JSON"))],
  ...[null, {}, { label: 123 }, { label: "" }, { label: " \n\t " }, { label: "x".repeat(81) }].map((body) => [JSON.stringify(body), () => Promise.resolve(new Response(JSON.stringify(body)))] as const),
] as const)("leaves the host untouched and logs nothing for invalid config: %s", async (_name, response) => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(response)
  const error = vi.spyOn(console, "error").mockImplementation(() => {})
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
  const log = vi.spyOn(console, "log").mockImplementation(() => {})
  const { spy, before } = install()
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce())
  // Drain fetch, JSON and terminal catch continuations before checking the negative result.
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(document.body.children).toHaveLength(1)
  expect(document.body.firstElementChild).toBe(before)
  expect(spy).not.toHaveBeenCalled()
  expect(error).not.toHaveBeenCalled(); expect(warn).not.toHaveBeenCalled(); expect(log).not.toHaveBeenCalled()
})

it("reserves the header's natural height and shrinks the iframe into the remaining panel space", async () => {
  server.use(http.get("https://desk.example/api/v1/public/example-cycle-works/embed-config", () => HttpResponse.json({ label: "Ask Example" })))
  const { roots } = install()
  await waitFor(() => expect(roots).toHaveLength(1))
  // jsdom has no geometry: verify the shipped layout rules on a copy. Browser bounds are reviewer-run.
  const sheet = document.createElement("style"); sheet.dataset.loaderLayout = ""
  sheet.textContent = roots[0]!.querySelector("style")!.textContent
  document.head.append(sheet)
  const panel = roots[0]!.querySelector("section")!.cloneNode(true) as HTMLElement
  panel.hidden = false; document.body.append(panel)
  expect(getComputedStyle(panel).display).toBe("flex")
  expect(getComputedStyle(panel).flexDirection).toBe("column")
  expect(getComputedStyle(panel.querySelector(".bar")!).flexShrink).toBe("0")
  const frame = getComputedStyle(panel.querySelector("iframe")!)
  expect(frame.flexGrow).toBe("1")
  expect(frame.minHeight).toBe("0px")
})

it("keeps the shipped loader within 5 KB gzip", async () => {
  const compressed = new Response(source).body!.pipeThrough(new CompressionStream("gzip"))
  expect((await new Response(compressed).arrayBuffer()).byteLength).toBeLessThanOrEqual(5000)
})

it.each(["  Ask Example  ", "x".repeat(80)])("mounts a valid trimmed label including the 80-character boundary: %s", async (label) => {
  server.use(http.get("https://desk.example/api/v1/public/example-cycle-works/embed-config", () => HttpResponse.json({ label })))
  const { roots } = install()
  await waitFor(() => expect(roots).toHaveLength(1))
  expect(within(roots[0]! as unknown as HTMLElement).getByRole("button", { name: label.trim() })).toBeInTheDocument()
})
