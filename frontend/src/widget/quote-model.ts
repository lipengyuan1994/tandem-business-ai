import type { Schemas } from "@/api/types"
import { parseMoneyInput } from "@/lib/money"

export interface QuoteDraft {
  prices: Record<string, string>; available: Record<string, string>; days: string; until: string; minimum: string; notes: string
}
export const blankQuote = (): QuoteDraft => ({ prices: {}, available: {}, days: "", until: "", minimum: "", notes: "" })
const quantity = (s: string) => /^\d+(?:\.\d{1,3})?$/.test(s) && Number(s) < 1e11
export function validQuote(d: QuoteDraft, lines: Schemas["RequestLineOut"][], currency: string) {
  return lines.length > 0 && lines.every((l) => { const p = parseMoneyInput(d.prices[l.item_id] ?? "", currency); const a = d.available[l.item_id] ?? ""; return p.ok && p.minor <= 1e12 && (!a || quantity(a)) }) && /^\d+$/.test(d.days) && Number(d.days) <= 3650 && (!d.minimum || (quantity(d.minimum) && Number(d.minimum) > 0))
}
export function quoteInput(d: QuoteDraft, lines: Schemas["RequestLineOut"][], currency: string): Omit<Schemas["PublicQuoteInput"], "declined"> {
  return { lines: lines.map((l) => { const p = parseMoneyInput(d.prices[l.item_id] ?? "", currency); if (!p.ok) throw new Error("Invalid price"); return { item_id: l.item_id, unit_price_minor: p.minor, quantity_available: d.available[l.item_id] || null } }), lead_time_days: Number(d.days), valid_until: d.until || null, min_quantity: d.minimum || null, notes: d.notes.trim() || null }
}
