import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { ApiProblem } from "@/lib/problem"
import { QueryError } from "./QueryError"

describe("QueryError", () => {
  const problem = (status: number) => new ApiProblem({ title: "x", status, code: status === 404 ? "crm.customer_not_found" : "server.error" })
  it("shows not found without a retry for a 404 when the page says what is missing", () => {
    render(<QueryError error={problem(404)} onRetry={vi.fn()} notFound="Customer not found" />)
    expect(screen.getByText("Customer not found")).toBeInTheDocument()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })
  it("offers a retry for other failures", () => {
    render(<QueryError error={problem(500)} onRetry={vi.fn()} notFound="Customer not found" />)
    expect(screen.getByRole("alert")).toBeInTheDocument()
    expect(screen.getByRole("button")).toBeInTheDocument()
  })
})
