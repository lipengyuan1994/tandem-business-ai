import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

describe("staff page", () => {
  it("invites a person and shows the temporary password once", async () => {
    mockMe()
    let body: unknown = null
    server.use(
      http.get("*/api/v1/identity/members", () => HttpResponse.json([
        { membership_id: "m1", user_id: "u1", display_name: "Morgan Example", email: "morgan@example.com", title: "Owner", status: "active", roles: ["owner"], version: 1 },
      ])),
      http.post("*/api/v1/identity/invitations", async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ id: "i1", email: "jordan@example.com", display_name: "Jordan Example", roles: ["operator"], status: "pending", expires_at: "2026-11-12T00:00:00Z", temporary_password: "not-a-real-password" }, { status: 201 })
      }),
    )
    renderApp("/settings/staff")
    expect(await screen.findByRole("cell", { name: "morgan@example.com" })).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Invite staff" }))
    const dialog = await screen.findByRole("dialog")
    await userEvent.type(within(dialog).getByLabelText("Email"), "jordan@example.com")
    await userEvent.type(within(dialog).getByLabelText("Name"), "Jordan Example")
    await userEvent.click(within(dialog).getByRole("button", { name: "Send invitation" }))
    expect(await within(dialog).findByText("not-a-real-password")).toBeInTheDocument()
    expect(body).toEqual({ email: "jordan@example.com", display_name: "Jordan Example", roles: ["operator"] })
  })
})

it("shows Demo reviewer and hides staff changes for the judge", async () => {
  const { ME } = await import("@/test/app")
  mockMe({ ...ME, permissions: ["org.business.read", "identity.membership.read"] })
  server.use(http.get("*/api/v1/identity/members", () => HttpResponse.json([
    { membership_id: "judge-m", user_id: "judge-u", display_name: "Demo Judge", email: "judge@example.com", title: "Demo reviewer", status: "active", roles: ["demo_reviewer"], version: 1 },
  ])))
  renderApp("/settings/staff")
  expect(await screen.findByRole("cell", { name: "Demo reviewer" })).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Invite staff" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Edit roles" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument()
})

it("offers the Demo reviewer label in both role pickers", async () => {
  mockMe()
  server.use(http.get("*/api/v1/identity/members", () => HttpResponse.json([
    { membership_id: "m1", user_id: "u1", display_name: "Demo Judge", email: "judge@example.com", status: "active", roles: ["demo_reviewer"], version: 1 },
  ])))
  renderApp("/settings/staff")
  await screen.findByRole("cell", { name: "Demo reviewer" })
  await userEvent.click(screen.getByRole("button", { name: "Edit roles" }))
  expect(await screen.findByRole("checkbox", { name: "Demo reviewer" })).toBeChecked()
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }))
  await userEvent.click(screen.getByRole("button", { name: "Invite staff" }))
  expect(await screen.findByRole("checkbox", { name: "Demo reviewer" })).toBeInTheDocument()
})
