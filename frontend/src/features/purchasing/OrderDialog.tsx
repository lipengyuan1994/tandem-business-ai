import { QueryError } from "@/components/common/QueryError"
import { quantityInput } from "@/lib/quantity"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { describeError, toastError } from "@/lib/errors"
import { minorToDecimal } from "@/lib/money"
import { FieldError } from "./FieldError"
import { fieldInvalid } from "./validation"
import { LinesEditor } from "./LinesEditor"
import { usePurchasingPickers, useSaveOrder, useSuppliers } from "./hooks"
import { blankLine, lineInputs, validLines, type LineDraft, type PO } from "./model"

export function OrderDialog({ order, initial, onClose, reload }: { order?: PO; initial?: { supplier: string; lines: LineDraft[] }; onClose: () => void; reload?: () => Promise<PO | undefined> }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const pickers = usePurchasingPickers()
  const suppliers = useSuppliers()
  const save = useSaveOrder(order?.id)
  const [supplier, setSupplier] = useState(order?.supplier_id ?? initial?.supplier ?? "")
  const [notes, setNotes] = useState(order?.notes ?? "")
  const [version, setVersion] = useState(order?.version)
  const [conflict, setConflict] = useState(false)
  const [lines, setLines] = useState<LineDraft[]>(() => order ? (order.lines ?? []).map((l) => ({ item: l.catalog_item_id, quantity: quantityInput(l.quantity), price: minorToDecimal(l.unit_cost_minor, order.currency) })) : initial?.lines ?? [blankLine()])
  const currency = order?.currency ?? pickers.data?.currency
  const ready = !!currency && !!supplier && validLines(lines, currency) && !pickers.error && !suppliers.error && !save.isPending && !conflict
  return <Dialog open onOpenChange={(open) => { if (!open) onClose() }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
    <DialogHeader><DialogTitle>{t(order ? "purchasing.editPO" : "purchasing.newPO")}</DialogTitle><DialogDescription>{t("purchasing.draftHint")}</DialogDescription></DialogHeader>
    <form id="po-form" className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (!ready || !currency) return
      save.mutate({ supplier_id: supplier, notes: notes || null, lines: lineInputs(lines, currency), ...(version ? { version } : {}) }, { onSuccess: (d) => { toast.success(t("common.saved")); onClose(); void navigate({ to: "/purchasing/purchase-orders/$id", params: { id: d.id } }) }, onError: (error) => toastError(error, () => setConflict(true)) })
    }}>
      <div><Label htmlFor="po-supplier">{t("purchasing.supplier")}</Label><select id="po-supplier" aria-invalid={fieldInvalid(save.error, "supplier_id")} className="h-9 w-full rounded-md border bg-background px-2" value={supplier} disabled={!!order} onChange={(e) => setSupplier(e.target.value)}>
        <option value="">{t("purchasing.chooseSupplier")}</option>{suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select><FieldError error={save.error} field="supplier_id" /></div>
      {pickers.error ? <QueryError error={pickers.error} onRetry={() => pickers.refetch()} /> : null}
      {suppliers.error ? <QueryError error={suppliers.error} onRetry={() => suppliers.refetch()} /> : null}
      {pickers.data && currency ? <LinesEditor lines={lines} onChange={setLines} items={pickers.data.items} currency={currency} error={save.error} /> : <p>{t("common.loading")}</p>}
      <div><Label htmlFor="po-notes">{t("purchasing.notes")}</Label><Textarea dictation={{ context: "purchasing" }} id="po-notes" maxLength={4000} aria-invalid={fieldInvalid(save.error, "notes")} value={notes} onChange={(e) => setNotes(e.target.value)} /><FieldError error={save.error} field="notes" /></div>
      {save.error ? <p role="alert" className="text-sm text-destructive">{describeError(save.error)}</p> : null}
    </form>
    <DialogFooter><Button variant="outline" onClick={onClose}>{t("common.cancel")}</Button><Button form="po-form" disabled={!ready}>{t(order ? "common.save" : "purchasing.createDraft")}</Button></DialogFooter>
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { void reload?.().then((d) => { if (d) { setVersion(d.version); setConflict(false) } }) }} />
  </DialogContent></Dialog>
}
