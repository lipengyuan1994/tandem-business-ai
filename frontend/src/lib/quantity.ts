/** API quantities have at most three fractional digits (ADR-011). */
export function formatQuantity(value: string | number): string {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 })
}

/** Unlocalized value for editable numeric fields; never insert grouping separators. */
export function quantityInput(value: string): string {
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value
}
