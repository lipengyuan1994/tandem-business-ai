/** API quantities have at most three fractional digits (ADR-011). */
export function formatQuantity(value: string | number): string {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 })
}

/** Unlocalized value for editable numeric fields; never insert grouping separators. */
export function quantityInput(value: string): string {
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value
}

/** Quantities are exact to the thousandth; work in thousandths as integers so nothing is lost to floating point. */
export const MILLI = 1000n

/** "2.5" → 2500n; null when the text is not a quantity with at most three decimals. */
export function quantityMilli(value: string): bigint | null {
  const m = /^(\d{1,11})(?:\.(\d{1,3}))?$/.exec(value.trim())
  if (!m) return null
  return BigInt(m[1]!) * MILLI + BigInt((m[2] ?? "").padEnd(3, "0"))
}
export function milliText(milli: bigint): string {
  const whole = milli / MILLI
  const frac = (milli % MILLI).toString().padStart(3, "0").replace(/0+$/, "")
  return frac ? `${whole}.${frac}` : whole.toString()
}
/** Round half up of a non-negative fraction, as the server's ROUND_HALF_UP does. */
export const halfUp = (numerator: bigint, denominator: bigint) => (2n * numerator + denominator) / (2n * denominator)
