import type { Schemas } from "@/api/types"
import { parseMoneyInput } from "@/lib/money"

export type PO = Schemas["POOut"]
export type Supplier = Schemas["SupplierOut"]
export type Suggestion = Schemas["PurchasingSuggestion"]
export const READ = "purchasing.po.read"
export const WRITE = "purchasing.po.write"
export const APPROVE = "purchasing.po.approve"
export const RECEIVE = "purchasing.po.receive"
export const OVERRIDE = "purchasing.receipt.override"
export interface LineDraft { item: string; quantity: string; price: string }
export const blankLine = (): LineDraft => ({ item: "", quantity: "1", price: "" })
export function validLines(lines: LineDraft[], currency: string) {
  return lines.length > 0 && lines.length <= 200 && lines.every((l) => l.item && /^\d+(?:\.\d{1,3})?$/.test(l.quantity) && Number(l.quantity) > 0 && Number(l.quantity) < 1e11 && (() => { const p = parseMoneyInput(l.price, currency); return p.ok && p.minor <= 10 ** 12 })())
}
export function lineInputs(lines: LineDraft[], currency: string) {
  return lines.map((l) => {
    const p = parseMoneyInput(l.price, currency)
    if (!p.ok) throw new Error("Invalid unit cost")
    return { catalog_item_id: l.item, quantity: l.quantity, unit_cost_minor: p.minor }
  })
}
