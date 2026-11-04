/** One source for UI noun defaults and i18next interpolation; data/schema names stay stable. */
export const DEFAULT_TERMS = {
  customer: "customer", customers: "customers", technician: "technician", technicians: "technicians",
  service: "service", services: "services", appointment: "appointment", appointments: "appointments",
}
export type TermKey = keyof typeof DEFAULT_TERMS

export function termVariables(overrides: Partial<Record<TermKey, string>> = {}) {
  return Object.fromEntries(Object.entries(DEFAULT_TERMS).flatMap(([key, fallback]) => {
    const value = overrides[key as TermKey] ?? fallback
    const titleKey = key.charAt(0).toUpperCase() + key.slice(1)
    return [[key, value], [titleKey, value.charAt(0).toUpperCase() + value.slice(1)]]
  }))
}
