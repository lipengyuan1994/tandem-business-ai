import type { TFunction } from "i18next"
import type { AgingReport } from "@/api/types"
import { decimalAmount, type CsvCell } from "@/lib/csv"
import type { SalesDocument } from "@/api/types"
import { canCredit } from "./credit"

export type DocumentFamily = "quotes" | "orders" | "invoices"
export const FAMILIES: DocumentFamily[] = ["quotes", "orders", "invoices"]
export const STATUSES: Record<DocumentFamily, string[]> = {
  quotes: ["draft", "sent", "accepted", "declined", "expired"],
  orders: ["draft", "confirmed", "fulfilled", "cancelled"],
  invoices: ["draft", "issued", "paid", "void"],
}
export const DOCUMENT_ROUTES = { quotes: "/sales/quotes/$id", orders: "/sales/orders/$id", invoices: "/sales/invoices/$id" } as const
export const PAYMENT_METHODS = ["cash", "card", "bank_transfer", "check", "other"] as const
export const RECORD_PAYMENT = "sales.invoice.mark_paid"
export const VOID_PAYMENT = "sales.invoice.credit"
export const AGING_BUCKETS = ["current_minor", "days_1_30_minor", "days_31_60_minor", "days_61_90_minor", "days_over_90_minor"] as const
export const READ = "sales.document.read"
export const WRITE = "sales.document.write"
export type SalesAction = "send" | "accept" | "decline" | "expire" | "convert" | "confirm" | "fulfil" | "cancel" | "invoice" | "issue" | "mark-paid" | "credit"
export function allowedActions(family: DocumentFamily, d: SalesDocument): SalesAction[] {
  if (family === "quotes") {
    if (d.status === "draft") return ["send"]
    if (d.status === "sent") return ["accept", "decline", "expire"]
    if (d.status === "accepted") return ["convert"]
  }
  if (family === "orders") {
    if (d.status === "draft") return ["confirm"]
    if (d.status === "confirmed") return ["fulfil", "cancel", "invoice"]
    if (d.status === "fulfilled") return ["invoice"]
  }
  if (family === "invoices" && d.kind !== "credit_note") {
    if (d.status === "draft") return ["issue"]
    // Credit notes can take some or all of an issued or paid invoice; any amount already paid beyond what is left
    // owed is refunded to the customer.
    if (d.status === "issued") return canCredit(d) ? ["mark-paid", "credit"] : ["mark-paid"]
    if (d.status === "paid") return canCredit(d) ? ["credit"] : []
  }
  return []
}
export function actionPermission(action: SalesAction): string {
  if (action === "convert" || action === "invoice") return WRITE
  if (action === "mark-paid") return "sales.invoice.mark_paid"
  if (action === "credit") return "sales.invoice.credit"
  return "sales.document.issue"
}
export function destination(family: DocumentFamily, action: SalesAction): DocumentFamily {
  return action === "convert" ? "orders" : action === "invoice" || action === "credit" ? "invoices" : family
}

/** One row per customer and currency, then a total row per currency; amounts as exact decimals. */
export function receivablesCsv(report: AgingReport, t: TFunction): CsvCell[][] {
  type Row = AgingReport["customers"][number] | AgingReport["totals"][number]
  const amounts = (row: Row, currency: string) => [...AGING_BUCKETS.map((b) => decimalAmount(row[b], currency)), decimalAmount(row.total_minor, currency)]
  return [
    [t("sales.aging.currency"), t("sales.fields.customer"), t("sales.aging.invoices"), ...AGING_BUCKETS.map((b) => t(`sales.aging.buckets.${b}`)), t("sales.aging.total")],
    ...report.totals.flatMap((total) => [
      ...report.customers.filter((c) => c.currency === total.currency).map((c) => [c.currency, c.customer_name, c.invoice_count, ...amounts(c, c.currency)]),
      [total.currency, t("sales.aging.total"), total.invoice_count, ...amounts(total, total.currency)],
    ]),
  ]
}
