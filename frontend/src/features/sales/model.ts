import type { SalesDocument } from "@/api/types"

export type DocumentFamily = "quotes" | "orders" | "invoices"
export const FAMILIES: DocumentFamily[] = ["quotes", "orders", "invoices"]
export const STATUSES: Record<DocumentFamily, string[]> = {
  quotes: ["draft", "sent", "accepted", "declined", "expired"],
  orders: ["draft", "confirmed", "fulfilled", "cancelled"],
  invoices: ["draft", "issued", "paid", "void"],
}
export const DOCUMENT_ROUTES = { quotes: "/sales/quotes/$id", orders: "/sales/orders/$id", invoices: "/sales/invoices/$id" } as const
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
    if (d.status === "issued") return ["mark-paid", "credit"]
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
