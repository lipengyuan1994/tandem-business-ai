import userEvent from "@testing-library/user-event"
import { screen } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { SystemStatus } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { formatAge } from "./hooks"

const OK: SystemStatus = {
  data_profile: "standard",
  version: "0.1.0", environment: "prod", checked_at: "2026-11-05T17:30:00Z", ai_usage_today: null,
  queue: { counts: { succeeded: 10 }, oldest_queued_age_seconds: null, dead_jobs: 0, dead_kinds: [], event_deliveries_dead: 0 },
  email: { configured: true, host: "mail.example.com" },
  outbox: { counts: { delivered: 4 }, needs_attention: 0 },
  backup: { available: true, result: "ok", last_success: "2026-11-05T15:30:00Z", age_seconds: 7200, snapshot_id: "a1b2c3d4", error: null },
}

describe("system page", () => {
  it("formats ages for people", () => {
    expect([formatAge(45), formatAge(300), formatAge(3 * 3600 + 12 * 60), formatAge(72 * 3600)]).toEqual(["45 s", "5 min", "3 h 12 min", "3 d 0 h"])
  })

  it("shows version, queue, messages, backup and that AI is not installed", async () => {
    mockMe(meWith("admin_ops.system.read"))
    server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json(OK)))
    renderApp("/system")
    expect(await screen.findByText("0.1.0")).toBeInTheDocument()
    expect(screen.getByText("The last backup succeeded.")).toBeInTheDocument()
    expect(screen.getByText(/Last successful backup 2 h 0 min ago/)).toBeInTheDocument()
    expect(screen.getByText("Nothing is waiting.")).toBeInTheDocument()
    expect(screen.getByText("The AI assistant is not installed.")).toBeInTheDocument()
    expect(screen.queryByText("Needs attention")).not.toBeInTheDocument()
  })

  it("flags overdue backups, dead tasks and messages that need attention", async () => {
    mockMe(meWith("admin_ops.system.read"))
    server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json({
      ...OK,
      queue: { counts: { dead: 2 }, oldest_queued_age_seconds: 1500, dead_jobs: 2, dead_kinds: ["notifications.deliver"], event_deliveries_dead: 0 },
      outbox: { counts: { unknown: 1 }, needs_attention: 1 },
      backup: { ...OK.backup, age_seconds: 9 * 3600 },
    })))
    renderApp("/system")
    expect(await screen.findByText("2 tasks failed for good (notifications.deliver)")).toBeInTheDocument()
    expect(screen.getByText("Oldest waiting task: 25 min")).toBeInTheDocument()
    expect(screen.getByText("1 message needs attention")).toBeInTheDocument()
    expect(screen.getByText("Backups should run every 6 hours: this one is overdue.")).toBeInTheDocument()
    expect(screen.getAllByText("Needs attention")).toHaveLength(3)
  })

  it("says so when there is no backup status", async () => {
    mockMe(meWith("admin_ops.system.read"))
    server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json({ ...OK, backup: { available: false, result: null, last_success: null, age_seconds: null, snapshot_id: null, error: null } })))
    renderApp("/system")
    expect(await screen.findByText("Status unavailable")).toBeInTheDocument()
    expect(screen.getByText(/Backup health cannot yet be confirmed/)).toBeInTheDocument()
    expect(screen.queryByText("Needs attention")).not.toBeInTheDocument()
  })
  it("offers the data download and the erasure list to owners only", async () => {
    mockMe(meWith("admin_ops.system.read", "admin_ops.data.manage"))
    server.use(
      http.get("*/api/v1/admin/system", () => HttpResponse.json(OK)),
      http.get("*/api/v1/admin/erasures", () => HttpResponse.json([{ id: "e1", customer_id: "0123456789ab", status: "completed", completed_at: "2026-11-05T18:00:00Z", requested_by: null, report: {} }])),
    )
    renderApp("/system")
    expect(await screen.findByRole("button", { name: "Download all data (ZIP)" })).toBeInTheDocument()
    expect(await screen.findByText(/Customer 01234567…/)).toBeInTheDocument()
  })

  it("hides the data panel without the data permission", async () => {
    mockMe(meWith("admin_ops.system.read"))
    server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json(OK)))
    renderApp("/system")
    await screen.findByText("0.1.0")
    expect(screen.queryByText("Your data")).not.toBeInTheDocument()
  })
})


describe("test e-mail", () => {
  it("opens a dialog, queues the recipient and reports queued delivery", async () => {
    const user = userEvent.setup()
    let recipient: unknown
    mockMe(meWith("admin_ops.system.read"))
    server.use(
      http.get("*/api/v1/admin/system", () => HttpResponse.json(OK)),
      http.post("*/api/v1/admin/test-email", async ({ request }) => {
        recipient = await request.json()
        return HttpResponse.json({ outbox_id: "test-id", status: "pending" }, { status: 202 })
      }),
    )
    renderApp("/system")
    expect(await screen.findByText("Configured: mail.example.com")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Send test e-mail" }))
    await user.type(screen.getByLabelText("Recipient e-mail"), "ada@example.com")
    await user.click(screen.getByRole("button", { name: "Send test" }))
    expect(await screen.findByText("Test e-mail queued. Check your inbox; delivery may take a moment.")).toBeInTheDocument()
    expect(recipient).toEqual({ to: "ada@example.com" })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("keeps the dialog open and shows a failure toast", async () => {
    const user = userEvent.setup()
    mockMe(meWith("admin_ops.system.read"))
    server.use(
      http.get("*/api/v1/admin/system", () => HttpResponse.json(OK)),
      http.post("*/api/v1/admin/test-email", () => HttpResponse.json({ code: "platform.rate_limited", title: "Too many requests", status: 429, detail: "Try again later", request_id: "test" }, { status: 429 })),
    )
    renderApp("/system")
    await user.click(await screen.findByRole("button", { name: "Send test e-mail" }))
    await user.type(screen.getByLabelText("Recipient e-mail"), "ada@example.com")
    await user.click(screen.getByRole("button", { name: "Send test" }))
    expect(await screen.findByText("Try again later")).toBeInTheDocument()
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })

  it("disables the action when SMTP is absent", async () => {
    mockMe(meWith("admin_ops.system.read"))
    server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json({ ...OK, email: { configured: false, host: null } })))
    renderApp("/system")
    expect(await screen.findByRole("button", { name: "Send test e-mail" })).toBeDisabled()
  })
})

it("shows regulated attestations and the compliance limit only in regulated installs", async () => {
  mockMe(meWith("admin_ops.system.read"))
  server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json({
    ...OK, data_profile: "regulated", regulated_host_baa: "Example host, signed 2027-01-10", regulated_backup_baa: "Example backups, signed 2027-01-10",
  })))
  renderApp("/system")
  expect(await screen.findByText("Regulated data profile")).toBeInTheDocument()
  expect(screen.getByText(/Host agreement: Example host/)).toBeInTheDocument()
  expect(screen.getByText(/Backup agreement: Example backups/)).toBeInTheDocument()
  expect(screen.getByText(/does not make the installation compliant/)).toHaveTextContent("legal review")
})


it.each([null, "never_run", "unknown", "ok"])("keeps incomplete backup telemetry neutral (%s)", async (result) => {
  mockMe(meWith("admin_ops.system.read"))
  server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json({ ...OK, backup: { ...OK.backup, result, age_seconds: null, last_success: null } })))
  renderApp("/system")
  expect(await screen.findByText("Status unavailable")).toBeInTheDocument()
  expect(screen.queryByText("The last backup failed.")).not.toBeInTheDocument()
  expect(screen.queryByText("Needs attention")).not.toBeInTheDocument()
})

it("identifies a confirmed backup failure separately from unavailable status", async () => {
  mockMe(meWith("admin_ops.system.read"))
  server.use(http.get("*/api/v1/admin/system", () => HttpResponse.json({ ...OK, backup: { ...OK.backup, result: "error", error: "Backup process failed" } })))
  renderApp("/system")
  expect(await screen.findByText("The last backup failed.")).toBeInTheDocument()
  expect(screen.getByText("Needs attention")).toBeInTheDocument()
  expect(screen.queryByText("Status unavailable")).not.toBeInTheDocument()
})
