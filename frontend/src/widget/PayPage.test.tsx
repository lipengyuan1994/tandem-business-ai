import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import { server } from "@/test/server"
import { parsePayLocation } from "./api"
import { PayPage } from "./PayPage"

// Synthetic data only.
const TOKEN = "cGF5MXxifGl8bA.c2lnbmF0dXJl"
const BASE = `*/api/v1/payments/pay/${TOKEN}`
const PAGE = {
  business_name: "Example Cycle Works", invoice_number: "INV-2026-0007", currency: "USD", total_minor: 6300, balance_minor: 6300,
  issue_date: "2026-11-20", due_date: "2026-12-04", state: "payable", test_mode: true,
}
function page(over: Partial<typeof PAGE> = {}) { server.use(http.get(BASE, () => HttpResponse.json({ ...PAGE, ...over }))) }
afterEach(() => { history.replaceState(null, "", "/"); vi.restoreAllMocks() })

describe("pay page", () => {
  it("reads the token from the address", () => {
    expect(parsePayLocation(`/pay/${TOKEN}`)).toBe(TOKEN)
    expect(parsePayLocation(`/pay/${TOKEN}/`)).toBe(TOKEN)
    expect(parsePayLocation("/pay/a/b")).toBeNull()
    expect(parsePayLocation("/q/abc")).toBeNull()
  })

  it("shows what is owed and sends the customer to Stripe's page", async () => {
    page()
    const assign = vi.fn()
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign, search: "" })
    let posts = 0
    server.use(http.post(`${BASE}/checkout`, () => { posts++; return HttpResponse.json({ url: "https://checkout.stripe.com/c/pay/cs_test_1" }) }))
    render(<PayPage token={TOKEN} />)
    expect(await screen.findByRole("heading", { name: "Pay Example Cycle Works" })).toBeInTheDocument()
    expect(screen.getByText("INV-2026-0007")).toBeInTheDocument()
    expect(screen.getByText("Test mode: no real money moves.")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Pay $63.00" }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.stripe.com/c/pay/cs_test_1"))
    expect(posts).toBe(1)
  })

  it.each([
    ["paid", "This invoice is paid. Thank you."],
    ["processing", "Your bank payment is on its way."],
    ["unavailable", "This invoice can't be paid online. Please contact Example Cycle Works."],
  ] as const)("says when the invoice is %s, with no pay button", async (state, text) => {
    page({ state })
    render(<PayPage token={TOKEN} />)
    expect(await screen.findByRole("status")).toHaveTextContent(text)
    expect(screen.queryByRole("button", { name: /Pay/ })).not.toBeInTheDocument()
  })

  it("tells the customer a replaced link no longer works", async () => {
    server.use(http.get(BASE, () => HttpResponse.json({ code: "payments.link_not_found" }, { status: 404 })))
    render(<PayPage token={TOKEN} />)
    expect(await screen.findByRole("alert")).toHaveTextContent("doesn't work any more")
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("reloads the page when the invoice changed since it was shown", async () => {
    let gets = 0
    server.use(
      http.get(BASE, () => HttpResponse.json({ ...PAGE, state: gets++ ? "paid" : "payable" })),
      http.post(`${BASE}/checkout`, () => HttpResponse.json({ code: "payments.invoice_settled" }, { status: 409 })),
    )
    render(<PayPage token={TOKEN} />)
    await userEvent.click(await screen.findByRole("button", { name: "Pay $63.00" }))
    expect(await screen.findByRole("status")).toHaveTextContent("This invoice is paid")
  })

  it.each([
    ["payments.relay_unavailable", "Something went wrong. Try again in a moment."],
    ["payments.stripe_refused", "Something went wrong. Try again in a moment."],
    ["payments.checkout_starting", "Your payment page is already opening"],
  ] as const)("shows why Stripe's page did not open (%s) and keeps the pay button", async (code, text) => {
    let gets = 0
    server.use(
      http.get(BASE, () => { gets++; return HttpResponse.json(PAGE) }),
      http.post(`${BASE}/checkout`, () => HttpResponse.json({ code }, { status: 409 })),
    )
    render(<PayPage token={TOKEN} />)
    await userEvent.click(await screen.findByRole("button", { name: "Pay $63.00" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(text)
    expect(screen.getByRole("button", { name: "Pay $63.00" })).toBeEnabled()
    expect(gets).toBe(1)
  })

  it("waits for Stripe's word after the customer comes back", async () => {
    history.replaceState(null, "", `/pay/${TOKEN}?paid=1`)
    let gets = 0
    server.use(http.get(BASE, () => HttpResponse.json({ ...PAGE, state: ++gets < 3 ? "payable" : "paid" })))
    render(<PayPage token={TOKEN} pollMs={10} />)
    expect(await screen.findByRole("status")).toHaveTextContent("confirming your payment")
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("This invoice is paid"))
    expect(gets).toBe(3)
  })
})
