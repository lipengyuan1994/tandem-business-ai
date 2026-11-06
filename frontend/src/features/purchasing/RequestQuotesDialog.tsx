import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useSession } from "@/app/session-context"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { formatInZone } from "@/lib/dates"
import { describeError } from "@/lib/errors"
import { FieldError } from "./FieldError"
import { fieldInvalid } from "./validation"
import { useProcurementAction, usePurchasingPickers, useSuppliers } from "./hooks"
import { ProposalFeedback } from "./ProposalFeedback"

export function RequestQuotesDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const pickers = usePurchasingPickers()
  const suppliers = useSuppliers()
  const save = useProcurementAction()
  const [lines, setLines] = useState([{ item_id: "", quantity: "1" }])
  const [chosen, setChosen] = useState<string[]>([])
  const [needed, setNeeded] = useState("")
  const [reply, setReply] = useState("")
  const [message, setMessage] = useState("")
  const [today] = useState(() => formatInZone(new Date().toISOString(), businessTimeZone, "yyyy-MM-dd"))
  const eligible = (suppliers.data ?? []).filter((s) => s.email && lines.every((l) => l.item_id && s.items?.some((i) => i.catalog_item_id === l.item_id)))
  const selected = chosen.filter((id) => eligible.some((s) => s.id === id))
  const valid = lines.length > 0 && lines.every((l) => l.item_id && /^\d+(?:\.\d{1,3})?$/.test(l.quantity) && Number(l.quantity) > 0 && Number(l.quantity) < 1e11) && new Set(lines.map((l) => l.item_id)).size === lines.length && selected.length >= 1 && selected.length <= 5 && reply >= today && needed >= reply && !!needed && !pickers.error && !suppliers.error
  return <Dialog open onOpenChange={(open) => { if (!open && !save.isPending) onClose() }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
    <DialogHeader><DialogTitle>{t("procurement.request")}</DialogTitle><DialogDescription>{t("procurement.approvalHint")}</DialogDescription></DialogHeader>
    {save.data ? <ProposalFeedback proposal={save.data} /> : <form id="quote-request" className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (valid && !save.isPending) save.mutate({ kind: "request", body: { lines, supplier_ids: selected, needed_by: needed, reply_by: reply, message: message.trim() || null } }) }}>
      {pickers.error ? <QueryError error={pickers.error} onRetry={() => pickers.refetch()} /> : !pickers.data ? <p>{t("common.loading")}</p> : null}
      {lines.map((line, i) => <fieldset key={i} className="space-y-2 rounded-md border p-3"><legend>{t("purchasing.line", { n: i + 1 })}</legend>
        <Label htmlFor={`qr-item-${i}`}>{t("purchasing.item")}</Label><select id={`qr-item-${i}`} className="h-11 w-full rounded-md border bg-background px-2" value={line.item_id} aria-invalid={fieldInvalid(save.error, `lines.${i}.item_id`)} onChange={(e) => { setLines(lines.map((l, n) => n === i ? { ...l, item_id: e.target.value } : l)); setChosen([]) }}>
          <option value="">{t("purchasing.chooseItem")}</option>{pickers.data?.items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><FieldError error={save.error} field={`lines.${i}.item_id`} />
        <Label htmlFor={`qr-qty-${i}`}>{t("purchasing.quantity")}</Label><Input id={`qr-qty-${i}`} inputMode="decimal" value={line.quantity} aria-invalid={fieldInvalid(save.error, `lines.${i}.quantity`)} onChange={(e) => setLines(lines.map((l, n) => n === i ? { ...l, quantity: e.target.value } : l))} /><FieldError error={save.error} field={`lines.${i}.quantity`} />
        <Button type="button" variant="outline" disabled={lines.length === 1} onClick={() => { setLines(lines.filter((_, n) => n !== i)); setChosen([]) }}>{t("purchasing.removeLine")}</Button>
      </fieldset>)}
      <Button type="button" variant="outline" disabled={lines.length >= 200} onClick={() => { setLines([...lines, { item_id: "", quantity: "1" }]); setChosen([]) }}>{t("purchasing.addLine")}</Button>
      <div className="grid gap-3 sm:grid-cols-2">{([["needed_by", needed, setNeeded, "needed"], ["reply_by", reply, setReply, "reply"]] as const).map(([field, value, set, label]) => <div key={field}><Label htmlFor={`qr-${field}`}>{t(`procurement.${label}`)}</Label><Input id={`qr-${field}`} type="date" required min={today} max={field === "reply_by" ? needed || undefined : undefined} value={value} aria-invalid={fieldInvalid(save.error, field)} onChange={(e) => set(e.target.value)} /><FieldError error={save.error} field={field} /></div>)}</div>
      <fieldset className="space-y-2"><legend>{t("procurement.chooseSuppliers")}</legend><p className="text-sm text-muted-foreground">{t("procurement.matchingSuppliers")}</p>
        {suppliers.error ? <QueryError error={suppliers.error} onRetry={() => suppliers.refetch()} /> : suppliers.isPending ? <p>{t("common.loading")}</p> : !eligible.length ? <p>{t("procurement.noSuppliers")}</p> : eligible.map((s) => <label key={s.id} className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={selected.includes(s.id)} disabled={selected.length >= 5 && !selected.includes(s.id)} onChange={(e) => setChosen(e.target.checked ? [...selected, s.id] : selected.filter((id) => id !== s.id))} />{s.name}</label>)}
        <FieldError error={save.error} field="supplier_ids" />
      </fieldset>
      <div><Label htmlFor="qr-message">{t("procurement.messageOptional")}</Label><Textarea dictation={{ context: "purchasing" }} id="qr-message" maxLength={2000} value={message} onChange={(e) => setMessage(e.target.value)} aria-invalid={fieldInvalid(save.error, "message")} /><FieldError error={save.error} field="message" /></div>
      {save.error ? <p role="alert">{describeError(save.error)}</p> : null}
    </form>}
    <DialogFooter><Button variant="outline" disabled={save.isPending} onClick={onClose}>{t(save.data ? "common.close" : "common.cancel")}</Button>{!save.data ? <Button form="quote-request" disabled={!valid || save.isPending}>{t("procurement.request")}</Button> : null}</DialogFooter>
  </DialogContent></Dialog>
}
