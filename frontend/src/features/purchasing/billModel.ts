import type { TFunction } from "i18next"
import { decimalAmount, type CsvCell } from "@/lib/csv"
import type { Schemas } from "@/api/types"

export type Bill = Schemas["BillOut"]
export type BillPayment = Schemas["BillPaymentOut"]
export type BillInput = Schemas["BillIn"]
export type BillPaymentInput = Schemas["BillPaymentIn"]
export type PayablesReport = Schemas["PayablesAgingReport"]
export type POBilling = Schemas["PurchaseOrderBilling"]
export type BillStatus = Bill["status"]

export const BILL_READ = "purchasing.bill.read"
export const BILL_WRITE = "purchasing.bill.write"
export const BILL_PAY = "purchasing.bill.pay"
export const BILL_VOID = "purchasing.bill.void"

export const BILL_STATUSES = ["open", "partially_paid", "paid", "void"] as const satisfies readonly BillStatus[]
/** "unpaid" is the default list: bills with money still owed. */
export const BILL_FILTERS = ["unpaid", "all", ...BILL_STATUSES] as const
export type BillFilter = (typeof BILL_FILTERS)[number]
export const filterStatuses = (filter: BillFilter): BillStatus[] | undefined =>
  filter === "all" ? undefined : filter === "unpaid" ? ["open", "partially_paid"] : [filter]

export const BILL_PAYMENT_METHODS = ["bank_transfer", "check", "card", "cash", "other"] as const satisfies readonly BillPayment["method"][]
export const PAYABLES_BUCKETS = ["current_minor", "days_1_30_minor", "days_31_60_minor", "days_61_90_minor", "days_over_90_minor"] as const
/** Purchase orders that can carry a supplier bill (backend BILLABLE_PO_STATUSES). */
export const BILLABLE_PO_STATUSES = ["approved", "sent", "partially_received", "received"]

/** One row per supplier and currency, then a total row per currency; amounts as exact decimals. */
export function payablesCsv(report: PayablesReport, t: TFunction): CsvCell[][] {
  type Row = PayablesReport["suppliers"][number] | PayablesReport["totals"][number]
  const amounts = (row: Row, currency: string) => [...PAYABLES_BUCKETS.map((b) => decimalAmount(row[b], currency)), decimalAmount(row.total_minor, currency)]
  return [
    [t("sales.aging.currency"), t("purchasing.supplier"), t("payables.bills"), ...PAYABLES_BUCKETS.map((b) => t(`sales.aging.buckets.${b}`)), t("sales.aging.total")],
    ...report.totals.flatMap((total) => [
      ...report.suppliers.filter((s) => s.currency === total.currency).map((s) => [s.currency, s.supplier_name, s.bill_count, ...amounts(s, s.currency)]),
      [total.currency, t("sales.aging.total"), total.bill_count, ...amounts(total, total.currency)],
    ]),
  ]
}
