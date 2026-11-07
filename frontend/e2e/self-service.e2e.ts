// Reviewer-run only: global setup reseeds synthetic data. No customer/appointment is created by this spec.
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { expect, test } from "@playwright/test"
import type { components } from "../src/api/schema.d.ts"
import { REPO_ROOT, storageState } from "./support/accounts.ts"
import { api } from "./support/api.ts"

type Settings = components["schemas"]["SettingsOut"]
type Appointment = components["schemas"]["AppointmentOut"]

test.use({ storageState: storageState("morgan") })

test("self-service: emailed link opens only the seeded customer's bookings and cancellation notifies staff", async ({ page, browser, baseURL }) => {
  const fixtures = JSON.parse(readFileSync(resolve(REPO_ROOT, "data/fixtures.json"), "utf8")) as {
    tables: {
      customers: { id: string; display_name: string }[]
      contact_points: { customer_id: string; type: string; value: string }[]
      appointments: { customer_id: string; business_id: string; status: string; starts_at: string }[]
    }
  }
  // Ben's tune-up is seeded, confirmed and upcoming. Select another seeded booking if its date has passed.
  const seeded = fixtures.tables.appointments.find((a) => a.business_id === "biz-cycle" && a.status === "confirmed" && Date.parse(a.starts_at) > Date.now())
  expect(seeded, "fixtures must contain an upcoming cycle-shop booking; reseed with current synthetic dates").toBeTruthy()
  const customer = fixtures.tables.customers.find((c) => c.id === seeded!.customer_id)!
  const email = fixtures.tables.contact_points.find((c) => c.customer_id === customer.id && c.type === "email")!.value
  const [match] = await api<{ customer_id: string }[]>(page.request, "GET", `/api/v1/crm/customers/match?email=${encodeURIComponent(email)}`)
  expect(match).toBeTruthy()
  const start = new Date().toISOString()
  const end = new Date(Date.now() + 60 * 86400000).toISOString()
  const appointments = await api<Appointment[]>(page.request, "GET", `/api/v1/scheduling/appointments?customer_id=${match!.customer_id}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&status=confirmed`)
  const appointment = appointments.find((a) => a.starts_at === new Date(seeded!.starts_at).toISOString()) ?? appointments[0]
  expect(appointment, "seeded customer has an upcoming confirmed booking").toBeTruthy()
  const original = await api<Settings>(page.request, "GET", "/api/v1/org/settings")
  const visitor = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, storageState: { cookies: [], origins: [] } })
  const recipient = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, storageState: { cookies: [], origins: [] } })
  try {
    // The seed already enables self-service. Turn it off first so this test exercises the owner's UI enable/save.
    await api(page.request, "PATCH", "/api/v1/org/settings", { version: original.version, settings: { ...original.settings, self_service_enabled: false } })
    await page.goto("/settings/policies")
    await page.getByRole("checkbox", { name: "Enable customer self-service" }).check()
    // Put every upcoming seeded appointment inside the notice window and require the acknowledgment path.
    await page.getByLabel("Cancellation notice (hours)").fill("8760")
    await page.getByLabel("Fee policy inside notice window (optional)").fill("Synthetic cancellation policy: contact the workshop about any fee.")
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect.poll(async () => (await api<Settings>(page.request, "GET", "/api/v1/org/settings")).settings.self_service_enabled).toBe(true)

    const request = await visitor.newPage()
    await request.goto("/p/example-cycle-works/bookings")
    await request.getByLabel("Email", { exact: true }).fill(email)
    await request.getByRole("button", { name: "Email me a sign-in link" }).click()
    await expect(request.getByText("If eligible bookings match that address, we'll email a sign-in link.")).toBeVisible()

    const mail = page.getByRole("region", { name: "Simulated e-mails" })
    await expect(async () => {
      await mail.getByRole("button", { name: "Refresh messages" }).click()
      await expect(mail.getByRole("listitem").filter({ hasText: email }).getByRole("link", { name: "Open customer sign-in link" }).first()).toBeVisible()
    }).toPass()
    const link = await mail.getByRole("listitem").filter({ hasText: email }).getByRole("link", { name: "Open customer sign-in link" }).first().getAttribute("href")
    expect(new URL(link!).origin).toBe(new URL(baseURL!).origin)
    expect(new URL(link!).hash).toMatch(/^#t=[A-Za-z0-9_-]{43}$/)
    const bookings = await recipient.newPage()
    await bookings.goto(link!)
    await expect(bookings.getByRole("heading", { name: "My bookings" })).toBeVisible()
    await expect(bookings.getByRole("button", { name: "Reload bookings" })).toBeVisible()
    await expect(bookings).not.toHaveURL(/#t=/) // capability removed after exchange
    const card = bookings.getByRole("listitem").filter({ has: bookings.getByRole("heading", { name: appointment!.service_name!, exact: true }) }).first()
    await expect(card.getByText("Confirmed", { exact: true })).toBeVisible()
    await card.getByRole("button", { name: "Cancel", exact: true }).click()
    const acknowledgment = card.getByRole("checkbox", { name: "I acknowledge this fee policy." })
    await expect(acknowledgment).toBeVisible()
    await expect(card.getByRole("button", { name: "Confirm cancellation" })).toBeDisabled()
    await acknowledgment.check()
    await card.getByRole("button", { name: "Confirm cancellation" }).click()
    await expect(bookings.getByText("Your booking was updated. A confirmation email has been queued.")).toBeVisible()
    await expect(card.getByText("Cancelled", { exact: true })).toBeVisible()

    await page.goto("/today")
    await page.getByRole("button", { name: /^Notifications/ }).click()
    await expect(page.getByText("Booking cancel by customer via self-service", { exact: true })).toBeVisible()
  } finally {
    const current = await api<Settings>(page.request, "GET", "/api/v1/org/settings")
    await api(page.request, "PATCH", "/api/v1/org/settings", { version: current.version, settings: { ...current.settings, self_service_enabled: original.settings.self_service_enabled, cancellation_notice_hours: original.settings.cancellation_notice_hours, cancellation_fee_text: original.settings.cancellation_fee_text } })
    await Promise.all([visitor.close(), recipient.close()])
  }
})
