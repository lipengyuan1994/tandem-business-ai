import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

const INVITE = { id: "i1", email: "jordan@example.com", display_name: "Jordan Example", roles: ["operator"], status: "pending", expires_at: "2026-12-01T00:00:00Z", temporary_password: null }

it("lists pending invitations and revokes one after confirming", async () => {
  mockMe()
  let pending = [INVITE]
  let revoked: string | null = null
  server.use(
    http.get("*/api/v1/identity/members", () => HttpResponse.json([])),
    http.get("*/api/v1/identity/invitations", () => HttpResponse.json(pending)),
    http.post("*/api/v1/identity/invitations/:id/revoke", ({ params }) => { revoked = params.id as string; pending = []; return new HttpResponse(null, { status: 204 }) }),
  )
  renderApp("/settings/staff")
  const list = within(await screen.findByRole("list", { name: "Pending invitations" }))
  expect(list.getByText("jordan@example.com")).toBeInTheDocument()
  await userEvent.click(list.getByRole("button", { name: "Revoke the invitation for Jordan Example" }))
  await userEvent.click(within(await screen.findByRole("dialog", { name: "Revoke the invitation for Jordan Example?" })).getByRole("button", { name: "Revoke" }))
  await waitFor(() => expect(revoked).toBe("i1"))
  await waitFor(() => expect(screen.queryByRole("list", { name: "Pending invitations" })).not.toBeInTheDocument())
})

it("shows invitations without revoke for read-only staff managers", async () => {
  mockMe({ ...meWith(), permissions: ["identity.membership.read", "org.business.read"] })
  server.use(
    http.get("*/api/v1/identity/members", () => HttpResponse.json([])),
    http.get("*/api/v1/identity/invitations", () => HttpResponse.json([INVITE])),
  )
  renderApp("/settings/staff")
  expect(await screen.findByText("jordan@example.com")).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /Revoke/ })).not.toBeInTheDocument()
})
