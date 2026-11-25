// Reports read other modules' data; each tab needs that module's read permission, and cost-based ones purchasing read.
import type { PermissionSet } from "@/lib/permissions"

export type ReportKind = "sales" | "margin" | "stock" | "spend"
export type Period = "day" | "week" | "month"
export const REPORTS: ReportKind[] = ["sales", "margin", "stock", "spend"]
const SALES_READ = "sales.document.read"
const STOCK_READ = "inventory.stock.read"
const PURCHASING_READ = "purchasing.po.read"
export const REPORT_PERMISSIONS: Record<ReportKind, string[]> = {
  sales: [SALES_READ],
  margin: [SALES_READ, PURCHASING_READ],
  stock: [STOCK_READ, PURCHASING_READ],
  spend: [PURCHASING_READ],
}

export function availableReports(permissions: PermissionSet): ReportKind[] {
  return REPORTS.filter((r) => REPORT_PERMISSIONS[r].every((p) => permissions.has(p)))
}

export const isDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value)

/** A report date range as typed: empty fields let the server choose (this month so far). */
export interface RangeInput { from: string; to: string }
export function rangeError(range: RangeInput): boolean {
  return isDate(range.from) && isDate(range.to) && range.to < range.from
}
export function rangeQuery(range: RangeInput): { date_from?: string; date_to?: string } {
  return { date_from: isDate(range.from) ? range.from : undefined, date_to: isDate(range.to) ? range.to : undefined }
}

/** Period keys are the first day of the period, as an ISO date in the business calendar. */
export function periodLabel(key: string, period: Period, locale?: string): string {
  const [y, m, d] = key.split("-").map(Number)
  if (!y || !m || !d) return key
  const date = new Date(Date.UTC(y, m - 1, d))
  const options: Intl.DateTimeFormatOptions = period === "month"
    ? { month: "long", year: "numeric", timeZone: "UTC" }
    : { day: "numeric", month: "short", year: "numeric", timeZone: "UTC", ...(period === "day" ? { weekday: "short" } : {}) }
  return new Intl.DateTimeFormat(locale, options).format(date)
}

/** 2550 basis points → "25.5%". */
export function percentText(basisPoints: number | null | undefined, locale?: string): string | null {
  if (basisPoints === null || basisPoints === undefined) return null
  return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 }).format(basisPoints / 10000)
}

export { decimalAmount, downloadCsv, toCsv, type CsvCell } from "@/lib/csv"
