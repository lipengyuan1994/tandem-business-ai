import "@testing-library/jest-dom/vitest"
import "@/app/i18n"
import { cleanup, configure } from "@testing-library/react"
import { afterAll, afterEach, beforeAll } from "vitest"
import { server } from "./server"

// findBy*/waitFor wait up to 5 s (default 1 s): with many jsdom workers on a busy machine (make check runs after the
// backend suite) 1 s is not always enough. Assertions are unchanged; only the patience is.
configure({ asyncUtilTimeout: 5000 })

// jsdom lacks ResizeObserver, which Radix primitives (checkbox, popover) use for sizing.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver

beforeAll(() => server.listen({ onUnhandledFrame: "error" }))
afterEach(() => {
  server.resetHandlers()
  cleanup()
})
afterAll(() => server.close())
