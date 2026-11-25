// Money travels as integer minor units plus an ISO 4217 code (ADR-011).
export interface Money {
  amount_minor: number
  currency: string
}

// ISO 4217 minor-unit exceptions, the same table as the backend (backend/app/platform/money.py). Every amount the
// API sends or accepts is in these units, so the browser's own Intl currency data (which drops the decimals of HUF,
// IDR, COP, IQD and others) must not decide how many digits a minor unit has.
const ZERO_DECIMAL = ["BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF"]
const THREE_DECIMAL = ["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"]
const currencyDigits = new Map<string, number>([
  ...ZERO_DECIMAL.map((code): [string, number] => [code, 0]),
  ...THREE_DECIMAL.map((code): [string, number] => [code, 3]),
])

export function minorUnitDigits(currency: string): number {
  return currencyDigits.get(currency) ?? 2
}

export type MoneyInputResult =
  | { ok: true; minor: number }
  | { ok: false; error: "invalid" | "precision" | "tooLarge"; digits: number }

/** Non-negative decimal input. Never multiply a floating-point amount or silently round extra digits. */
export function parseMoneyInput(value: string, currency: string): MoneyInputResult {
  const digits = minorUnitDigits(currency)
  const input = value.trim()
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(input)) return { ok: false, error: "invalid", digits }
  const [whole = "", fraction = ""] = input.split(".")
  if (fraction.length > digits) return { ok: false, error: "precision", digits }
  const minor = BigInt(`${whole || "0"}${fraction.padEnd(digits, "0")}`)
  if (minor > BigInt(Number.MAX_SAFE_INTEGER)) return { ok: false, error: "tooLarge", digits }
  // Converting an already bounded integer to number is exact; JSON APIs cannot carry bigint.
  return { ok: true, minor: Number(minor) }
}

/** Editable decimal amount with the currency's full precision, without grouping or currency symbols. */
export function minorToDecimal(minor: number, currency: string): string {
  if (!Number.isSafeInteger(minor)) throw new RangeError("Money must use safe integer minor units")
  const digits = minorUnitDigits(currency)
  const integer = BigInt(minor)
  const sign = integer < 0n ? "-" : ""
  const absolute = (integer < 0n ? -integer : integer).toString().padStart(digits + 1, "0")
  if (digits === 0) return `${sign}${absolute}`
  return `${sign}${absolute.slice(0, -digits)}.${absolute.slice(-digits)}`
}

export function formatMoney(money: Money, locale = "en-US"): string {
  const digits = minorUnitDigits(money.currency)
  const formatter = new Intl.NumberFormat(locale, {
    style: "currency",
    currency: money.currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  return formatter.format(money.amount_minor / 10 ** digits)
}
