import { useTranslation } from "react-i18next"
import type { CatalogItem } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { FieldError } from "./FieldError"
import { fieldInvalid } from "./validation"
import { blankLine, type LineDraft } from "./model"

export function LinesEditor({ lines, onChange, items, currency, error }: { lines: LineDraft[]; onChange: (lines: LineDraft[]) => void; items: CatalogItem[]; currency: string; error?: unknown }) {
  const { t } = useTranslation()
  const change = (index: number, patch: Partial<LineDraft>) => onChange(lines.map((line, i) => i === index ? { ...line, ...patch } : line))
  return <div className="space-y-3">{lines.map((line, i) => <fieldset key={i} className="grid gap-3 rounded-md border p-3 sm:grid-cols-3">
    <legend className="px-1 text-sm">{t("purchasing.line", { n: i + 1 })}</legend>
    <div><Label htmlFor={`po-item-${i}`}>{t("purchasing.item")}</Label><select id={`po-item-${i}`} aria-invalid={fieldInvalid(error, `lines.${i}.catalog_item_id`)} className="h-9 w-full rounded-md border bg-background px-2" value={line.item} onChange={(e) => change(i, { item: e.target.value })}>
      <option value="">{t("purchasing.chooseItem")}</option>{items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select><FieldError error={error} field={`lines.${i}.catalog_item_id`} /></div>
    <div><Label htmlFor={`po-qty-${i}`}>{t("purchasing.quantity")}</Label><Input id={`po-qty-${i}`} aria-invalid={fieldInvalid(error, `lines.${i}.quantity`)} inputMode="decimal" value={line.quantity} onChange={(e) => change(i, { quantity: e.target.value })} /><FieldError error={error} field={`lines.${i}.quantity`} /></div>
    <div><Label htmlFor={`po-cost-${i}`}>{t("purchasing.cost", { currency })}</Label><Input id={`po-cost-${i}`} aria-invalid={fieldInvalid(error, `lines.${i}.unit_cost_minor`)} inputMode="decimal" value={line.price} onChange={(e) => change(i, { price: e.target.value })} /><FieldError error={error} field={`lines.${i}.unit_cost_minor`} /></div>
    <Button type="button" variant="outline" disabled={lines.length === 1} onClick={() => onChange(lines.filter((_, index) => index !== i))}>{t("purchasing.removeLine")}</Button>
  </fieldset>)}<Button type="button" variant="outline" disabled={lines.length >= 200} onClick={() => onChange([...lines, blankLine()])}>{t("purchasing.addLine")}</Button></div>
}
