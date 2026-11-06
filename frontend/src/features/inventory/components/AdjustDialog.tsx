import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { toastError } from "@/lib/errors"
import { type AdjustReason, useAdjustStock } from "../hooks"

const REASONS: AdjustReason[] = ["receipt", "adjustment_gain", "adjustment_loss", "count_correction", "return"]
const NEGATIVE = new Set<AdjustReason>(["adjustment_loss"])

/** Reason is required; the quantity is a change ("+2", "1 lost"), never a new total. */
export function AdjustDialog({ itemId, itemName, onOpenChange }: { itemId: string; itemName: string; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const adjust = useAdjustStock()
  const [reason, setReason] = useState<AdjustReason | "">("")
  const [quantity, setQuantity] = useState("1")
  const [note, setNote] = useState("")
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    const n = Number(quantity)
    if (!reason) return setError(t("inventory.adjust.reasonRequired"))
    if (!Number.isFinite(n) || n === 0) return setError(t("inventory.adjust.quantityRequired"))
    const signed = NEGATIVE.has(reason) ? -Math.abs(n) : n
    adjust.mutate({ item_id: itemId, quantity: signed, reason, note: note || null }, { onSuccess: () => onOpenChange(false), onError: (e) => toastError(e) })
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("inventory.adjust.title", { name: itemName })}</DialogTitle>
          <DialogDescription>{t("inventory.adjust.hint")}</DialogDescription>
        </DialogHeader>
        <form id="adjust-form" className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <div className="space-y-1">
            <Label htmlFor="adj-reason">{t("inventory.adjust.reason")}</Label>
            <select id="adj-reason" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={reason} onChange={(e) => setReason(e.target.value as AdjustReason)}>
              <option value="">{t("inventory.adjust.chooseReason")}</option>
              {REASONS.map((r) => <option key={r} value={r}>{t(`inventory.reasons.${r}`)}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="adj-qty">{t("inventory.adjust.quantity")}</Label>
            <Input id="adj-qty" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="adj-note">{t("inventory.adjust.note")}</Label>
            <Textarea dictation={{ context: "inventory" }} id="adj-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button type="submit" form="adjust-form" disabled={adjust.isPending}>{t("inventory.adjust.submit")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
