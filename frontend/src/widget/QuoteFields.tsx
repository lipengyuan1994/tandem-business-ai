import type { Schemas } from "@/api/types"
import S from "virtual:supplier-quote-copy"
import type { QuoteDraft } from "./quote-model"
const input = "mt-1 block min-h-11 w-full min-w-0 rounded-md border bg-background px-3 py-2"
export function QuoteFields({ draft: d, onChange, lines, currency, errors = {} }: { draft: QuoteDraft; onChange: (d: QuoteDraft) => void; lines: Schemas["RequestLineOut"][]; currency: string; errors?: Record<string, string> }) {
  const field = (name: string) => Object.keys(errors).some((k) => k === name || k === `body.${name}`)
  return <>
    {lines.map((line, i) => <fieldset key={line.item_id} className="space-y-3 rounded-md border p-3"><legend className="px-1">{line.name} · {line.quantity}</legend>
      <label className="block">{S.price.replace("{currency}", currency)} · {line.name}<input className={input} required inputMode="decimal" value={d.prices[line.item_id] ?? ""} aria-invalid={field(`lines.${i}.unit_price_minor`)} onChange={(e) => onChange({ ...d, prices: { ...d.prices, [line.item_id]: e.target.value } })} /></label>
      <label className="block">{S.available} · {line.name}<input className={input} inputMode="decimal" value={d.available[line.item_id] ?? ""} aria-invalid={field(`lines.${i}.quantity_available`)} onChange={(e) => onChange({ ...d, available: { ...d.available, [line.item_id]: e.target.value } })} /></label>
    </fieldset>)}
    <label className="block">{S.days}<input className={input} required inputMode="numeric" value={d.days} aria-invalid={field("lead_time_days")} onChange={(e) => onChange({ ...d, days: e.target.value })} /></label>
    <label className="block">{S.until}<input className={input} type="date" value={d.until} aria-invalid={field("valid_until")} onChange={(e) => onChange({ ...d, until: e.target.value })} /></label>
    <label className="block">{S.minimum}<input className={input} inputMode="decimal" value={d.minimum} aria-invalid={field("min_quantity")} onChange={(e) => onChange({ ...d, minimum: e.target.value })} /></label>
    <label className="block">{S.notes}<textarea className={input} maxLength={500} rows={3} value={d.notes} aria-invalid={field("notes")} onChange={(e) => onChange({ ...d, notes: e.target.value })} /></label>
    {Object.keys(errors).length ? <p role="alert">{S.validation}</p> : null}
  </>
}
