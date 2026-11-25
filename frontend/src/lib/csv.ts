// CSV downloads made in the browser from data a screen already shows (reports, aging).
import { minorUnitDigits } from "@/lib/money"

/** Exact decimal text of minor units for CSV, e.g. -1234 USD → "-12.34". */
export function decimalAmount(minor: number, currency: string): string {
  const digits = minorUnitDigits(currency)
  const sign = minor < 0 ? "-" : ""
  const text = String(Math.abs(minor)).padStart(digits + 1, "0")
  return digits ? `${sign}${text.slice(0, -digits)}.${text.slice(-digits)}` : `${sign}${text}`
}

export type CsvCell = string | number | null | undefined
/** RFC 4180 CSV. Text that a spreadsheet would run as a formula is prefixed with an apostrophe. */
export function toCsv(rows: CsvCell[][]): string {
  const cell = (value: CsvCell) => {
    if (value === null || value === undefined) return ""
    let text = String(value)
    if (typeof value === "string" && /^[=+\-@\t\r]/.test(text) && !/^-?\d+(\.\d+)?$/.test(text)) text = `'${text}`
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  return rows.map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n"
}

export function downloadCsv(filename: string, rows: CsvCell[][]): void {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
