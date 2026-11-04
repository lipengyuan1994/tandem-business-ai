import { describe, expect, it } from "vitest"
import { formatQuantity, quantityInput } from "./quantity"

describe("quantity formatting", () => {
  it.each([["1.000", "1"], ["2.500", "2.5"], ["10.000", "10"], ["0.001", "0.001"], ["-2.050", "-2.05"]])("formats %s without losing fractional units", (raw, expected) => {
    expect(formatQuantity(raw)).toBe(expected)
    expect(quantityInput(raw)).toBe(expected)
  })
  it("keeps numeric input values free of locale separators", () => {
    expect(quantityInput("10000.500")).toBe("10000.5")
  })
})
