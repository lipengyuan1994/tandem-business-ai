import { describe, expect, it } from "vitest"
import { formatMoney, minorToDecimal, minorUnitDigits, parseMoneyInput } from "./money"

describe("exact money input", () => {
  it.each([
    ["25", "USD", 2500], ["25.5", "USD", 2550], ["25.00", "USD", 2500],
    ["0.1", "USD", 10], ["0.2", "USD", 20], ["0.29", "USD", 29], ["0.30", "USD", 30],
    [".01", "USD", 1], ["0", "USD", 0], ["2500", "JPY", 2500],
    ["25", "KWD", 25000], ["25.5", "KWD", 25500], ["25.123", "KWD", 25123],
    ["90071992547409.91", "USD", Number.MAX_SAFE_INTEGER],
  ])("converts %s %s to %i minor units", (value, currency, minor) => {
    expect(parseMoneyInput(value, currency)).toEqual({ ok: true, minor })
  })

  it("keeps 0.1 and 0.2 exact and rejects a floating-point artifact instead of rounding it", () => {
    expect(parseMoneyInput("0.1", "USD")).toEqual({ ok: true, minor: 10 })
    expect(parseMoneyInput("0.2", "USD")).toEqual({ ok: true, minor: 20 })
    expect(parseMoneyInput(String(0.1 + 0.2), "USD")).toMatchObject({ ok: false, error: "precision", digits: 2 })
  })

  it.each([["25.001", "USD", 2], ["1.0", "JPY", 0], ["1.2345", "KWD", 3]])("rejects extra decimal places in %s %s", (value, currency, digits) => {
    expect(parseMoneyInput(value, currency)).toEqual({ ok: false, error: "precision", digits })
  })

  it.each(["", " ", "-1", "1e2", "NaN", "1,000.00", "0.1+0.2", "."])("rejects malformed or negative input %s", (value) => {
    expect(parseMoneyInput(value, "USD")).toMatchObject({ ok: false, error: "invalid" })
  })

  it("rejects amounts that cannot be sent as safe integer minor units", () => {
    expect(parseMoneyInput("90071992547409.92", "USD")).toMatchObject({ ok: false, error: "tooLarge" })
  })

  it.each([
    [2500, "USD", "25.00"], [2550, "USD", "25.50"], [10, "USD", "0.10"],
    [0, "USD", "0.00"], [2500, "JPY", "2500"], [25123, "KWD", "25.123"],
    [2500, "KWD", "2.500"], [Number.MAX_SAFE_INTEGER, "USD", "90071992547409.91"],
  ])("displays API price %i %s as %s and round-trips exactly", (minor, currency, decimal) => {
    expect(minorToDecimal(minor, currency)).toBe(decimal)
    expect(parseMoneyInput(decimal, currency)).toEqual({ ok: true, minor })
  })
})

describe("minor units follow the backend's ISO 4217 table, not the browser's currency data", () => {
  it.each([
    ["USD", 2], ["EUR", 2], ["HUF", 2], ["IDR", 2], ["COP", 2], ["PKR", 2],
    ["JPY", 0], ["KRW", 0], ["CLP", 0], ["ISK", 0], ["IQD", 3], ["KWD", 3],
  ])("%s has %i decimal places", (currency, digits) => {
    expect(minorUnitDigits(currency)).toBe(digits)
  })

  it("reads 1000 HUF as 100000 minor units, as the API counts them", () => {
    expect(parseMoneyInput("1000", "HUF")).toEqual({ ok: true, minor: 100000 })
    expect(parseMoneyInput("1000.50", "HUF")).toEqual({ ok: true, minor: 100050 })
    expect(minorToDecimal(100050, "HUF")).toBe("1000.50")
    expect(parseMoneyInput("1000", "IQD")).toEqual({ ok: true, minor: 1000000 })
  })

  it("formats amounts with the same number of decimal places", () => {
    expect(formatMoney({ amount_minor: 100050, currency: "HUF" })).toBe("HUF\u00a01,000.50")
    expect(formatMoney({ amount_minor: 1000000, currency: "IQD" })).toBe("IQD\u00a01,000.000")
    expect(formatMoney({ amount_minor: 2500, currency: "JPY" })).toBe("¥2,500")
    expect(formatMoney({ amount_minor: 2500, currency: "USD" })).toBe("$25.00")
  })
})
