import type { SalesDocument, SalesLine } from "@/api/types"

/** Quantities are exact to the thousandth; work in thousandths as integers so nothing is lost to floating point. */
const MILLI = 1000n
const RATE = 10000n

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
const halfUp = (numerator: bigint, denominator: bigint) => (2n * numerator + denominator) / (2n * denominator)
const rateTenThousandths = (rate: string) => BigInt(Math.round(Number(rate) * 10000))

export interface CreditableLine { line: SalesLine; leftMilli: bigint; leftSubtotal: number; leftTax: number }

/** Lines with something left to credit, and how much is left of each. */
export function creditableLines(d: SalesDocument): CreditableLine[] {
  return (d.lines ?? []).flatMap((line) => {
    const total = quantityMilli(String(line.quantity)) ?? 0n
    const done = quantityMilli(String(line.credited_quantity ?? "0")) ?? 0n
    const leftMilli = total - done
    if (leftMilli <= 0n) return []
    return [{
      line, leftMilli,
      leftSubtotal: line.line_subtotal_minor - (line.credited_subtotal_minor ?? 0),
      leftTax: line.line_tax_minor - (line.credited_tax_minor ?? 0),
    }]
  })
}

/** What crediting `milli` of a line comes to, exactly as the server works it out. */
export function creditAmount(c: CreditableLine, milli: bigint): number {
  if (milli <= 0n) return 0
  if (milli === c.leftMilli) return c.leftSubtotal + c.leftTax
  const subtotal = halfUp(milli * BigInt(c.line.unit_price_minor), MILLI)
  const tax = halfUp(subtotal * rateTenThousandths(String(c.line.tax_rate)), RATE)
  const min = (a: bigint, b: number) => Number(a < BigInt(b) ? a : BigInt(b))
  return min(subtotal, c.leftSubtotal) + min(tax, c.leftTax)
}

/** The credit takes what is still owed first; the rest goes back to the customer as a refund. */
export function refundFor(d: SalesDocument, credit: number): number {
  return Math.max(0, credit - (d.balance_minor ?? 0))
}

/** An issued or paid invoice can be credited while any of its lines has something left. */
export function canCredit(d: SalesDocument): boolean {
  return d.kind !== "credit_note" && (d.status === "issued" || d.status === "paid")
    && d.total_minor - (d.amount_credited_minor ?? 0) > 0 && creditableLines(d).length > 0
}
