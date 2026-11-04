import { quantityInput } from "@/lib/quantity"
import type { SalesLine, SalesLineInput } from "@/api/types"
import { minorToDecimal, parseMoneyInput } from "@/lib/money"

export interface DraftLine { key: string; catalog_item_id: string; name: string; quantity: string; price: string; tax: string }
export const blankLine = (): DraftLine => ({ key: crypto.randomUUID(), catalog_item_id: "", name: "", quantity: "1", price: "", tax: "" })
export const MAX_UNIT_PRICE_MINOR = 1e12
export function draftLines(lines: SalesLine[], currency: string): DraftLine[] {
  return lines.map((l) => ({ key: l.id, catalog_item_id: l.catalog_item_id ?? "", name: l.name, quantity: quantityInput(l.quantity), price: minorToDecimal(l.unit_price_minor, currency), tax: String(Math.round(Number(l.tax_rate) * 10000) / 100) }))
}
export function validLines(lines: DraftLine[], currency: string): boolean {
  return lines.length > 0 && lines.length <= 200 && lines.every((l) => {
    const price = parseMoneyInput(l.price, currency)
    return (
      /^\d+(\.\d{1,3})?$/.test(l.quantity) && Number(l.quantity) > 0 && Number(l.quantity) < 1e11 &&
      (!!l.catalog_item_id || (l.name.trim().length > 0 && price.ok && price.minor <= MAX_UNIT_PRICE_MINOR)) &&
      (l.tax === "" || (/^\d+(\.\d{1,2})?$/.test(l.tax) && Number(l.tax) <= 100))
    )
  })
}
export function lineInputs(lines: DraftLine[], currency: string): SalesLineInput[] {
  return lines.map((l) => {
    const input: SalesLineInput = {
      quantity: l.quantity,
      // An omitted rate snapshots the business default. Explicit null is rejected; zero must be preserved.
      ...(l.tax === "" ? {} : { tax_rate: (Number(l.tax) / 100).toFixed(4) }),
    }
    if (l.catalog_item_id) return { ...input, catalog_item_id: l.catalog_item_id }
    const price = parseMoneyInput(l.price, currency)
    if (!price.ok || price.minor > MAX_UNIT_PRICE_MINOR) throw new RangeError("Invalid unit price")
    return { ...input, name: l.name.trim(), unit_price_minor: price.minor }
  })
}
export function hasFieldError(errors: Record<string, string>, field: string) {
  return Object.keys(errors).some((key) => key === field || key === `body.${field}` || key.startsWith(`${field}.`) || key.startsWith(`body.${field}.`))
}

