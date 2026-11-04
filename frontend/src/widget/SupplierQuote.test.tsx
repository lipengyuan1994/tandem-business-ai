import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { server } from "@/test/server"
import { SupplierQuote } from "./SupplierQuote"
import { parseQuoteLocation } from "./api"
const BASE = "*/api/v1/public/quotes/test.token"
const request = { business_name: "Example Cycle Works", currency: "USD", needed_by: "2099-11-20", reply_by: "2099-11-10", message: "Please quote", lines: [{ item_id: "item-1", name: "Example pads", quantity: "2" }] }
describe("supplier quote page", () => {
  it("parses capability routes without treating them as business slugs", () => {
    expect(parseQuoteLocation("/q/test.token")).toBe("test.token")
    expect(parseQuoteLocation("/q/test.token/")).toBe("test.token")
    expect(parseQuoteLocation("/q/a/b")).toBeNull()
  })
  it("submits normal currency prices and shows a thank-you", async () => {
    let body: unknown
    server.use(http.get(BASE, () => HttpResponse.json(request)), http.post(BASE, async ({ request }) => { body = await request.json(); return HttpResponse.json({ status: "responded" }) }))
    render(<SupplierQuote token="test.token" />)
    expect(await screen.findByRole("heading", { name: "Quote for Example Cycle Works" })).toBeInTheDocument()
    const submit = screen.getByRole("button", { name: "Send quote" })
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Unit price (USD) · Example pads"), "12.50")
    await userEvent.type(screen.getByLabelText("Quantity available · Example pads"), "8")
    await userEvent.type(screen.getByLabelText("Lead time (days)"), "3")
    await userEvent.type(screen.getByLabelText("Valid until (optional)"), "2099-11-30")
    await userEvent.type(screen.getByLabelText("Notes (optional)"), "In stock")
    await userEvent.click(submit)
    await waitFor(() => expect(body).toEqual({ declined: false, lines: [{ item_id: "item-1", unit_price_minor: 1250, quantity_available: "8" }], lead_time_days: 3, valid_until: "2099-11-30", min_quantity: null, notes: "In stock" }))
    expect(await screen.findByRole("status")).toHaveTextContent("Thank you")
  })
  it("can decline without entering prices", async () => {
    let body: unknown
    server.use(http.get(BASE, () => HttpResponse.json(request)), http.post(BASE, async ({ request }) => { body = await request.json(); return HttpResponse.json({ status: "declined" }) }))
    render(<SupplierQuote token="test.token" />)
    await userEvent.click(await screen.findByLabelText("We cannot quote"))
    await userEvent.click(screen.getByRole("button", { name: "Send response" }))
    await waitFor(() => expect(body).toEqual({ declined: true }))
    expect(await screen.findByRole("status")).toHaveTextContent("Thank you")
  })
  it("explains an expired or invalid token", async () => {
    server.use(http.get(BASE, () => HttpResponse.json({ code: "purchasing.invalid_quote_token" }, { status: 422 })))
    render(<SupplierQuote token="test.token" />)
    expect(await screen.findByRole("alert")).toHaveTextContent("expired or is invalid")
    expect(screen.queryByRole("button", { name: "Send quote" })).not.toBeInTheDocument()
  })
  it("retains inputs and offers retry after a network error", async () => {
    server.use(http.get(BASE, () => HttpResponse.json(request)), http.post(BASE, () => HttpResponse.error()))
    render(<SupplierQuote token="test.token" />)
    await userEvent.type(await screen.findByLabelText("Unit price (USD) · Example pads"), "1.234")
    await userEvent.type(screen.getByLabelText("Lead time (days)"), "0")
    expect(screen.getByRole("button", { name: "Send quote" })).toBeDisabled()
    await userEvent.clear(screen.getByLabelText("Unit price (USD) · Example pads"))
    await userEvent.type(screen.getByLabelText("Unit price (USD) · Example pads"), "1.23")
    await userEvent.click(screen.getByRole("button", { name: "Send quote" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("try again")
    expect(screen.getByLabelText("Unit price (USD) · Example pads")).toHaveValue("1.23")
    expect(screen.getByRole("button", { name: "Send quote" })).toBeEnabled()
  })
})
