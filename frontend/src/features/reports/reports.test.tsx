import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import type { MarginReport, SalesReport, SpendReport, StockValueReport } from "@/api/types"
import { meWith, mockMe, renderApp } from "@/test/app"
import { server } from "@/test/server"
import { decimalAmount, periodLabel, percentText, toCsv } from "./model"

const ALL = ["sales.document.read", "purchasing.po.read", "inventory.stock.read"]

const SALES: SalesReport = {
  date_from: "2026-09-01", date_to: "2026-10-31", group_by: "period", period: "month",
  rows: [
    { key: "2026-09-01", label: "2026-09-01", currency: "USD", invoice_count: 1, credit_note_count: 0, net_minor: 5000, tax_minor: 400, total_minor: 5400, quantity: null },
    { key: "2026-10-01", label: "2026-10-01", currency: "USD", invoice_count: 3, credit_note_count: 1, net_minor: 9800, tax_minor: 784, total_minor: 10584, quantity: null },
  ],
  totals: [{ currency: "USD", invoice_count: 4, credit_note_count: 1, net_minor: 14800, tax_minor: 1184, total_minor: 15984 }],
}
const SALES_BY_ITEM: SalesReport = {
  ...SALES, group_by: "item",
  rows: [
    { key: "i1", label: "Brake pads", currency: "USD", invoice_count: 2, credit_note_count: 0, net_minor: 6000, tax_minor: 480, total_minor: 6480, quantity: "3.000" },
    { key: "free_text", label: "", currency: "USD", invoice_count: 1, credit_note_count: 0, net_minor: 3300, tax_minor: 264, total_minor: 3564, quantity: "1.000" },
  ],
}

function serveSales() {
  const asked: URLSearchParams[] = []
  server.use(http.get("*/api/v1/sales/reports/sales", ({ request }) => {
    const params = new URL(request.url).searchParams
    asked.push(params)
    return HttpResponse.json(params.get("group_by") === "item" ? SALES_BY_ITEM : SALES)
  }))
  return asked
}

describe("reports page", () => {
  it("shows sales by month with totals, and asks again for a new grouping and range", async () => {
    mockMe(meWith(...ALL))
    const asked = serveSales()
    renderApp("/reports")
    const table = await screen.findByRole("table", { name: "Sales in USD" })
    const [, september, october, total] = within(table).getAllByRole("row")
    expect(within(september!).getByText("September 2026")).toBeInTheDocument()
    expect(within(october!).getByText("$98.00")).toBeInTheDocument()
    expect(within(total!).getByText("$148.00")).toBeInTheDocument()
    expect(asked[0]!.get("group_by")).toBe("period")
    expect(asked[0]!.get("date_from")).toBeNull()

    await userEvent.selectOptions(screen.getByLabelText("Group by"), "item")
    expect(await screen.findByText("Brake pads")).toBeInTheDocument()
    expect(screen.getByText("Free-text lines")).toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: "Quantity" })).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText("From"), "2026-09-01")
    await userEvent.type(screen.getByLabelText("To"), "2026-10-31")
    await waitFor(() => expect(asked.at(-1)!.get("date_to")).toBe("2026-10-31"))
    expect(asked.at(-1)!.get("date_from")).toBe("2026-09-01")
  })

  it("stops on a reversed range without asking the server", async () => {
    mockMe(meWith(...ALL))
    const asked = serveSales()
    renderApp("/reports")
    await screen.findByRole("table", { name: "Sales in USD" })
    await userEvent.type(screen.getByLabelText("From"), "2026-10-31")
    await userEvent.type(screen.getByLabelText("To"), "2026-10-01")
    expect(await screen.findByRole("alert")).toHaveTextContent("The start date must be on or before the end date.")
    expect(asked.every((p) => p.get("date_to") !== "2026-10-01")).toBe(true)
  })

  it("shows gross margin, leaving uncosted sales out of the margin", async () => {
    mockMe(meWith(...ALL))
    serveSales()
    const report: MarginReport = {
      date_from: "2026-10-01", date_to: "2026-10-31", group_by: "item", currency: "USD", cost_basis: "average_purchase_cost", other_currency_document_count: 1,
      rows: [
        { key: "i1", label: "Brake pads", kind: "product", quantity: "3.000", revenue_minor: 6000, costed_revenue_minor: 6000, uncosted_revenue_minor: 0, cost_minor: 3300, margin_minor: 2700, margin_basis_points: 4500 },
        { key: "free_text", label: "", kind: "free_text", quantity: "1.000", revenue_minor: 3800, costed_revenue_minor: 0, uncosted_revenue_minor: 3800, cost_minor: null, margin_minor: null, margin_basis_points: null },
      ],
      totals: { revenue_minor: 9800, costed_revenue_minor: 6000, uncosted_revenue_minor: 3800, cost_minor: 3300, margin_minor: 2700, margin_basis_points: 4500 },
    }
    server.use(http.get("*/api/v1/sales/reports/margin", () => HttpResponse.json(report)))
    renderApp("/reports")
    await userEvent.click(await screen.findByRole("tab", { name: "Gross margin" }))
    const table = await screen.findByRole("table", { name: "Gross margin in USD" })
    const [, pads, freeText] = within(table).getAllByRole("row")
    expect(within(pads!).getByText("45%")).toBeInTheDocument()
    expect(within(freeText!).getAllByText("—").length).toBeGreaterThanOrEqual(3)
    expect(screen.getByText(/\$38\.00 of sales has no known cost/)).toBeInTheDocument()
    expect(screen.getByText("1 invoice in another currency is not included.")).toBeInTheDocument()
  })

  it("shows stock value by location", async () => {
    mockMe(meWith(...ALL))
    serveSales()
    const asked: (string | null)[] = []
    const report: StockValueReport = {
      as_of: "2026-10-31", group_by: "location", currency: "USD", cost_basis: "average_purchase_cost",
      rows: [{ key: "l1", label: "Main shop", sku: null, unit: null, quantity: null, unit_cost_minor: null, value_minor: 16500, item_count: 2, uncosted_item_count: 1 }],
      totals: { value_minor: 16500, item_count: 2, uncosted_item_count: 1 },
    }
    server.use(http.get("*/api/v1/inventory/reports/stock-value", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("group_by"))
      return HttpResponse.json(report)
    }))
    renderApp("/reports")
    await userEvent.click(await screen.findByRole("tab", { name: "Stock value" }))
    await userEvent.selectOptions(await screen.findByLabelText("Group by"), "location")
    expect(await screen.findByText("Main shop")).toBeInTheDocument()
    expect(asked).toContain("location")
    expect(screen.getByText("1 item on hand has no purchase price yet, so it is not valued.")).toBeInTheDocument()
  })

  it("shows only the reports the user can read", async () => {
    mockMe(meWith("purchasing.po.read"))
    const report: SpendReport = {
      date_from: "2026-10-01", date_to: "2026-10-31", group_by: "supplier", period: "month",
      rows: [{ key: "s1", label: "Parts Co", currency: "USD", order_count: 2, ordered_minor: 23000, receipt_count: 2, received_minor: 16500 }],
      totals: [{ currency: "USD", order_count: 2, ordered_minor: 23000, receipt_count: 2, received_minor: 16500 }],
    }
    server.use(http.get("*/api/v1/purchasing/reports/spend", () => HttpResponse.json(report)))
    renderApp("/reports")
    const table = await screen.findByRole("table", { name: "Spend in USD" })
    expect(within(table).getByText("Parts Co")).toBeInTheDocument()
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Spend"])
    expect(screen.getByRole("link", { name: "Reports" })).toBeInTheDocument()
  })

  it("says when no report is available", async () => {
    mockMe(meWith("crm.customer.read"))
    renderApp("/reports")
    expect(await screen.findByText("You don't have access to any reports.")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Reports" })).not.toBeInTheDocument()
  })
})

describe("report helpers", () => {
  it("labels periods by their first day", () => {
    expect(periodLabel("2026-10-01", "month", "en-US")).toBe("October 2026")
    expect(periodLabel("2026-10-19", "week", "en-US")).toBe("Oct 19, 2026")
  })
  it("formats exact amounts and percentages", () => {
    expect(decimalAmount(-1234, "USD")).toBe("-12.34")
    expect(decimalAmount(5, "USD")).toBe("0.05")
    expect(decimalAmount(1500, "JPY")).toBe("1500")
    expect(percentText(2550, "en-US")).toBe("25.5%")
    expect(percentText(null)).toBeNull()
  })
  it("writes CSV that spreadsheets read as data", () => {
    expect(toCsv([["Name", "Net"], ['Ada "A", Ltd', "-12.34"], ["=SUM(A1)", 3]])).toBe('Name,Net\r\n"Ada ""A"", Ltd",-12.34\r\n\'=SUM(A1),3\r\n')
  })
})
