import { QueryError } from "@/components/common/QueryError"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCatalogItems } from "@/features/catalog/hooks"
import { minorToDecimal, parseMoneyInput } from "@/lib/money"

import { blankLine, hasFieldError, MAX_UNIT_PRICE_MINOR, type DraftLine } from "../line-draft"

export function LinesEditor({ lines, currency, onChange, errors = {} }: { lines: DraftLine[]; currency: string; onChange: (lines: DraftLine[]) => void; errors?: Record<string, string> }) {
  const { t } = useTranslation()
  const { permissions } = useSession()
  return <fieldset className="space-y-3">
    <legend className="font-medium">{t("sales.lines")}</legend>
    <p className="text-xs text-muted-foreground">{t("sales.linesHint")}</p>
    {lines.map((l, index) => {
      const set = (patch: Partial<DraftLine>) => onChange(lines.map((old) => old.key === l.key ? { ...l, ...patch } : old))
      const price = parseMoneyInput(l.price, currency)
      const priceError = !l.catalog_item_id && l.price !== "" ? (
        !price.ok ? t(`sales.priceErrors.${price.error}`, { currency, digits: price.digits })
          : price.minor > MAX_UNIT_PRICE_MINOR ? t("sales.priceErrors.tooLarge") : undefined
      ) : undefined
      const input = (field: "name" | "quantity" | "price" | "tax", apiField: string, label: string, props: { type?: string; inputMode?: "decimal"; min?: number; max?: number; step?: string; maxLength?: number; disabled?: boolean; placeholder?: string } = {}) => {
        const message = field === "price" ? priceError : undefined
        const invalid = hasFieldError(errors, `lines.${index}.${apiField}`) || !!message
        const id = `${l.key}-${field}`
        return <div className="space-y-1">
          <Label htmlFor={id}>{label}</Label>
          <Input id={id} {...props} value={l[field]} onChange={(e) => set({ [field]: e.target.value })} aria-invalid={invalid} aria-describedby={invalid ? `${id}-error` : undefined} />
          {invalid ? <p id={`${id}-error`} className="text-sm text-destructive">{message ?? t("sales.invalidField")}</p> : null}
        </div>
      }
      return <fieldset key={l.key} className="space-y-3 rounded-md border p-3">
        <legend className="px-1 text-sm">{t("sales.lineNumber", { n: index + 1 })}</legend>
        {permissions.has("catalog.item.read") ? <CatalogPicker line={l} currency={currency} onChange={set} /> : null}
        {l.catalog_item_id && !permissions.has("catalog.item.read") ? <Button type="button" variant="outline" onClick={() => set({ catalog_item_id: "" })}>{t("sales.useFreeText")}</Button> : null}
        {input("name", "name", t("sales.fields.description"), { disabled: !!l.catalog_item_id, maxLength: 200 })}
        <div className="grid gap-3 sm:grid-cols-3">
          {input("quantity", "quantity", t("sales.fields.quantity"), { type: "number", min: 0.001, step: "0.001" })}
          {input("price", "unit_price_minor", t("sales.fields.price", { currency }), { inputMode: "decimal", maxLength: 32, disabled: !!l.catalog_item_id })}
          {input("tax", "tax_rate", t("sales.fields.taxPercent"), { type: "number", min: 0, max: 100, step: "0.01", placeholder: t("sales.businessDefault") })}
        </div>
        {l.catalog_item_id ? <p className="text-xs text-muted-foreground">{t("sales.catalogHint")}</p> : null}
        <Button type="button" variant="ghost" size="sm" onClick={() => onChange(lines.filter((old) => old.key !== l.key))}>{t("sales.removeLine", { n: index + 1 })}</Button>
      </fieldset>
    })}
    <Button type="button" variant="outline" disabled={lines.length >= 200} onClick={() => onChange([...lines, blankLine()])}>{t("sales.addLine")}</Button>
  </fieldset>
}
function CatalogPicker({ line, currency, onChange }: { line: DraftLine; currency: string; onChange: (patch: Partial<DraftLine>) => void }) {
  const { t } = useTranslation()
  const catalog = useCatalogItems(undefined)
  const items = catalog.data?.items.filter((item) => item.status === "active" && item.price_minor !== null && item.currency === currency) ?? []
  return <div className="space-y-1">
    <Label htmlFor={`${line.key}-catalog`}>{t("sales.fields.catalog")}</Label>
    <select id={`${line.key}-catalog`} className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={line.catalog_item_id} onChange={(e) => {
      const item = items.find((i) => i.id === e.target.value)
      onChange(item && item.price_minor !== null ? { catalog_item_id: item.id, name: item.name, price: minorToDecimal(item.price_minor, currency) } : { catalog_item_id: "" })
    }}>
      <option value="">{t("sales.freeText")}</option>
      {line.catalog_item_id && !items.some((i) => i.id === line.catalog_item_id) ? <option value={line.catalog_item_id}>{line.name}</option> : null}
      {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>
    {catalog.isPending ? <p role="status">{t("common.loading")}</p> : null}
    {catalog.error ? <QueryError error={catalog.error} onRetry={() => catalog.refetch()} /> : null}
  </div>
}
