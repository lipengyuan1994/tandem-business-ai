import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it, vi } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { normalizeEmbedOrigins } from "./embed-origins"

function setup(write = true) {
  mockMe(meWith("org.settings.read", "crm.customer.read", ...(write ? ["org.settings.write"] : [])))
  server.use(http.get("*/api/v1/org/business", () => HttpResponse.json({ name: "Example Cycle Works", slug: "example-cycle-works", currency: "USD" })), http.get("*/api/v1/org/settings", () => HttpResponse.json({ version: 1, settings: { terminology: {}, sales_tax_rate: "0", ai_policy: "hosted_vultr", embed_allowed_origins: [], embed_button_label: "Ask Example", self_service_enabled: false, cancellation_notice_hours: 24, cancellation_fee_text: null } })), http.get("*/api/v1/notifications/simulated-emails", () => HttpResponse.json([])))
}

it("validates exact origins and normalizes default ports", () => {
  expect(normalizeEmbedOrigins("https://EXAMPLE.com:443\nhttps://example.com", false)).toEqual(["https://example.com"])
  for (const value of ["https://*.example.com", "https://example.com:0", "http://example.com", "https://example.com/path", "https://a:b@example.com", "https://example.com/", "https://example.com?", "https://example.com#", "http://localhost:3000"]) expect(() => normalizeEmbedOrigins(value, false)).toThrow()
  expect(normalizeEmbedOrigins("http://localhost:3000", true)).toEqual(["http://localhost:3000"])
  expect(normalizeEmbedOrigins("http://localhost:80", true)).toEqual(["http://localhost"])
  expect(() => normalizeEmbedOrigins(Array.from({ length: 11 }, (_, i) => `https://h${i}.example`).join("\n"), false)).toThrow()
})

it("copies snippet and saves website/self-service settings while preserving unrelated settings", async () => {
  setup(); let sent: unknown
  server.use(http.patch("*/api/v1/org/settings", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ version: 2, settings: { terminology: {}, ai_policy: "hosted_vultr", sales_tax_rate: "0" } }) }))
  renderApp("/settings/policies")
  const user = userEvent.setup()
  const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue()
  await screen.findByLabelText("Website embed snippet")
  await waitFor(() => expect(screen.getByLabelText("Website embed snippet")).toHaveValue(`<script src="${location.origin}/embed.js" data-business="example-cycle-works" async></script>`))
  await user.click(screen.getByRole("button", { name: "Copy snippet" }))
  expect(clipboard).toHaveBeenCalledWith(expect.stringContaining('data-business="example-cycle-works"'))
  await user.type(screen.getByLabelText("Allowed website origins"), "https://SHOP.example:443")
  await user.click(screen.getByRole("checkbox", { name: "Enable customer self-service" }))
  await user.clear(screen.getByLabelText("Cancellation notice (hours)")); await user.type(screen.getByLabelText("Cancellation notice (hours)"), "12")
  await user.type(screen.getByLabelText("Fee policy inside notice window (optional)"), "A $10 fee applies")
  await user.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(sent).toMatchObject({ version: 1, settings: { embed_allowed_origins: ["https://shop.example"], self_service_enabled: true, cancellation_notice_hours: 12, cancellation_fee_text: "A $10 fee applies", ai_policy: "hosted_vultr" } }))
})

it("disables save for invalid origins and permits correction", async () => {
  setup(); renderApp("/settings/policies")
  await userEvent.type(await screen.findByLabelText("Allowed website origins"), "https://*.example.com")
  expect(await screen.findByRole("alert")).toHaveTextContent("exact HTTPS origins")
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  await userEvent.clear(screen.getByLabelText("Allowed website origins"))
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled()
})

it("renders read-only controls without settings write permission", async () => {
  setup(false); renderApp("/settings/policies")
  expect(await screen.findByLabelText("Allowed website origins")).toBeDisabled()
  expect(screen.getByRole("checkbox", { name: "Enable customer self-service" })).toBeDisabled()
  expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()
  expect(screen.queryByText("Simulated e-mails")).not.toBeInTheDocument()
})

it.each(["populated", "empty", "failed"])("shows %s simulated email view", async (state) => {
  setup()
  server.use(http.get("*/api/v1/notifications/simulated-emails", () => state === "failed" ? HttpResponse.json({ code: "failed" }, { status: 500 }) : HttpResponse.json(state === "empty" ? [] : [{ id: "m1", recipient: "ben@example.com", subject: "Your bookings", body: `View bookings: ${location.origin}/p/example-cycle-works/bookings#t=${"a".repeat(43)}`, status: "pending", created_at: "2026-11-06T17:00:00Z" }])))
  renderApp("/settings/policies")
  await screen.findByText("Simulated e-mails")
  if (state === "failed") expect(await screen.findByRole("alert")).toHaveTextContent("Could not load simulated")
  else if (state === "empty") expect(await screen.findByText(/No simulated e-mails/)).toBeInTheDocument()
  else expect(await screen.findByRole("link", { name: "Open customer sign-in link" })).toHaveAttribute("href", `${location.origin}/p/example-cycle-works/bookings#t=${"a".repeat(43)}`)
})
