import { describe, expect, it } from "vitest"
import { formatInZone, localToUtcIso, zoneAbbreviation } from "./dates"
import { formatMoney } from "./money"
import { hasPermission } from "./permissions"
import { ApiProblem, toApiProblem } from "./problem"

describe("dates", () => {
  it("formats a UTC instant in the business time zone, independent of the browser zone", () => {
    expect(formatInZone("2026-11-10T17:00:00Z", "America/Denver", "yyyy-MM-dd HH:mm")).toBe("2026-11-10 10:00")
    expect(formatInZone("2026-11-10T17:00:00Z", "Europe/London", "HH:mm")).toBe("17:00")
    expect(zoneAbbreviation("2026-11-10T17:00:00Z", "America/Denver")).toBe("MST")
  })

  it("converts local wall-clock input to UTC across DST changes", () => {
    expect(localToUtcIso("2026-11-10", "10:00", "America/Denver")).toBe("2026-11-10T17:00:00.000Z") // MST, UTC-7
    expect(localToUtcIso("2026-10-27", "10:00", "America/Denver")).toBe("2026-10-27T16:00:00.000Z") // MDT, UTC-6
    expect(() => localToUtcIso("2026-11-xx", "10:00", "America/Denver")).toThrow()
  })
})

describe("money", () => {
  it("uses the currency's minor units", () => {
    expect(formatMoney({ amount_minor: 6500, currency: "USD" })).toBe("$65.00")
    expect(formatMoney({ amount_minor: 650, currency: "GBP" }, "en-GB")).toBe("£6.50")
    expect(formatMoney({ amount_minor: 1200, currency: "JPY" }, "en-US")).toBe("¥1,200")
  })
})

describe("problem details", () => {
  it("wraps problem bodies and exposes field errors", () => {
    const p = toApiProblem({ title: "Validation failed", status: 422, code: "platform.validation_error", errors: [{ field: "email", code: "value_error" }] }, 422)
    expect(p).toBeInstanceOf(ApiProblem)
    expect(p.fieldErrors()).toEqual({ email: "value_error" })
    expect(toApiProblem("<html>", 502).code).toBe("client.unexpected_response")
    expect(toApiProblem({ title: "Conflict", status: 409, code: "crm.customer_version_conflict" }, 409).isConflict).toBe(true)
  })
})

describe("permissions", () => {
  it("treats a missing permission requirement as allowed", () => {
    expect(hasPermission(new Set(), undefined)).toBe(true)
    expect(hasPermission(new Set(["crm.customer.read"]), "crm.customer.read")).toBe(true)
    expect(hasPermission(new Set(), "crm.customer.read")).toBe(false)
  })
})
