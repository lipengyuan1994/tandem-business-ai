import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { expect, it } from "vitest"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"

it.each([
  ["JPY", "100", 100, "100.1", 0],
  ["KWD", "1.234", 1234, "1.2345", 3],
  ["USD", "12.34", 1234, "12.345", 2],
])("catalog prices use %s minor units and reject excess precision", async (currency, amount, minor, invalid, digits) => {
  mockMe(meWith("catalog.item.read", "catalog.item.write"))
  let sent: unknown = null
  server.use(
    http.get("*/api/v1/org/business", () => HttpResponse.json({ currency })),
    http.get("*/api/v1/catalog/items", () => HttpResponse.json({ items: [], next_cursor: null })),
    http.post("*/api/v1/catalog/items", async ({ request }) => { sent = await request.json(); return HttpResponse.json({ id: "s1" }, { status: 201 }) }),
  )
  renderApp("/catalog")
  await userEvent.click(await screen.findByRole("button", { name: "New service" }))
  await userEvent.type(screen.getByLabelText("Name"), "Example service")
  const price = screen.getByLabelText(/Price/)
  await userEvent.type(price, invalid)
  await userEvent.click(screen.getByRole("button", { name: "Save" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(`${currency} allows up to ${digits} decimal places.`)
  expect(sent).toBeNull()
  await userEvent.clear(price)
  await userEvent.type(price, amount)
  await userEvent.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(sent).toMatchObject({ price_minor: minor }))
})
