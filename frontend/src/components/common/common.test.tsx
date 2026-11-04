import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ColumnDef } from "@tanstack/react-table"
import { describe, expect, it, vi } from "vitest"
import { renderWithSession } from "@/test/render"
import { DataTable } from "./DataTable"
import { LocalTime } from "./LocalTime"
import { MoneyText } from "./MoneyText"
import { PermissionGate } from "./PermissionGate"
import { StatusBadge } from "./StatusBadge"

interface Row { name: string }
const columns: ColumnDef<Row, unknown>[] = [{ accessorKey: "name", header: "Name" }]

describe("StatusBadge", () => {
  it("translates known statuses and uses the shared tone", () => {
    render(<StatusBadge status="pending_review" />)
    const badge = screen.getByText("Pending review")
    expect(badge).toHaveAttribute("data-tone", "pending")
  })
  it("falls back to a readable label for unknown statuses", () => {
    render(<StatusBadge status="some_new_state" />)
    expect(screen.getByText("some new state")).toHaveAttribute("data-tone", "neutral")
  })
})

describe("LocalTime and MoneyText", () => {
  it("shows local time with the zone and keeps the UTC instant in dateTime", () => {
    render(<LocalTime iso="2026-11-10T17:00:00Z" timeZone="America/Denver" pattern="HH:mm" />)
    const el = screen.getByText("10:00 MST")
    expect(el.tagName).toBe("TIME")
    expect(el).toHaveAttribute("datetime", "2026-11-10T17:00:00Z")
  })
  it("renders money and a dash for missing values", () => {
    const { rerender } = render(<MoneyText value={{ amount_minor: 2500, currency: "USD" }} />)
    expect(screen.getByText("$25.00")).toBeInTheDocument()
    rerender(<MoneyText value={null} />)
    expect(screen.getByText("—")).toBeInTheDocument()
  })
})

describe("PermissionGate", () => {
  it("renders children only with the permission", () => {
    const { unmount } = renderWithSession(<PermissionGate permission="crm.customer.write">secret</PermissionGate>, [])
    expect(screen.queryByText("secret")).not.toBeInTheDocument()
    unmount()
    renderWithSession(<PermissionGate permission="crm.customer.write">secret</PermissionGate>, ["crm.customer.write"])
    expect(screen.getByText("secret")).toBeInTheDocument()
  })
})

describe("DataTable", () => {
  it("shows loading, empty, error and data states", async () => {
    const onRetry = vi.fn()
    const { rerender } = render(<DataTable columns={columns} data={undefined} isLoading />)
    expect(screen.getByLabelText("Loading…")).toHaveAttribute("aria-busy", "true")

    rerender(<DataTable columns={columns} data={[]} emptyTitle="No customers" />)
    expect(screen.getByText("No customers")).toBeInTheDocument()

    rerender(<DataTable columns={columns} data={undefined} error={new Error("boom")} onRetry={onRetry} />)
    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(onRetry).toHaveBeenCalledOnce()

    const onLoadMore = vi.fn()
    rerender(<DataTable columns={columns} data={[{ name: "Ada Example" }]} hasMore onLoadMore={onLoadMore} />)
    expect(screen.getByRole("cell", { name: "Ada Example" })).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Load more" }))
    expect(onLoadMore).toHaveBeenCalledOnce()
  })
})


it("gives records a native link reachable by Tab, with a focusable scroll region", async () => {
  render(<DataTable columns={columns} data={[{ name: "Ada Example" }]} rowHref={() => "/customers/ada"} caption="Customers" />)
  const region = screen.getByRole("region", { name: "Scrollable table" })
  expect(region).toHaveAttribute("tabindex", "0")
  const link = screen.getByRole("link", { name: "Ada Example" })
  expect(link).toHaveAttribute("href", "/customers/ada")
  await userEvent.tab()
  expect(region).toHaveFocus()
  await userEvent.tab()
  expect(link).toHaveFocus()
})

it("uses a keyboard-operable button when a record opens a detail sheet", async () => {
  const open = vi.fn()
  render(<DataTable columns={columns} data={[{ name: "Stock adjusted" }]} onRowClick={open} />)
  screen.getByRole("button", { name: "Stock adjusted" }).focus()
  await userEvent.keyboard("{Enter}")
  expect(open).toHaveBeenCalledExactlyOnceWith({ name: "Stock adjusted" })
})

it("announces failed queries with a retry instead of an empty-data message", async () => {
  const retry = vi.fn()
  render(<DataTable columns={columns} data={[]} error={new Error("Service unavailable")} onRetry={retry} emptyTitle="No customers" />)
  expect(screen.getByRole("alert")).toHaveTextContent("Service unavailable")
  expect(screen.queryByText("No customers")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Try again" })).toHaveAttribute("type", "button")
  await userEvent.click(screen.getByRole("button", { name: "Try again" }))
  expect(retry).toHaveBeenCalledOnce()
})

it("keeps column headings on one line unless the table asks them to wrap", () => {
  const { rerender } = render(<DataTable columns={columns} data={[{ name: "Ada Example" }]} caption="Customers" />)
  expect(screen.getByRole("columnheader", { name: "Name" })).toHaveClass("whitespace-nowrap")
  rerender(<DataTable columns={columns} data={[{ name: "Ada Example" }]} caption="Customers" wrapHeaders />)
  const heading = screen.getByRole("columnheader", { name: "Name" })
  expect(heading).toHaveClass("whitespace-normal")
  expect(heading).not.toHaveClass("whitespace-nowrap") // the later class replaces the default, so long headings can wrap
})
