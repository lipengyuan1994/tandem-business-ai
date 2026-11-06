import { quantityInput } from "@/lib/quantity"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import type { ActionFormProps } from "./types"

const REASONS = ["receipt", "adjustment_gain", "adjustment_loss", "count_correction", "return"] as const

/** inventory.adjust_stock: signed change, reason and note (the item stays fixed). */
export function AdjustStockForm({ payload, pending, onSubmit, onCancel }: ActionFormProps) {
  const { t } = useTranslation()
  const [quantity, setQuantity] = useState(quantityInput(String(payload.quantity ?? "")))
  const [reason, setReason] = useState(String(payload.reason ?? "adjustment_gain"))
  const [note, setNote] = useState(String(payload.note ?? ""))
  const valid = quantity !== "" && Number.isFinite(Number(quantity)) && Number(quantity) !== 0
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid) onSubmit({ ...payload, quantity, reason, note: note || null }) }}>
      <div className="space-y-1">
        <Label htmlFor="adj-form-qty">{t("approvals.forms.signedQuantity")}</Label>
        <Input id="adj-form-qty" inputMode="decimal" value={quantity} aria-invalid={!valid} onChange={(e) => setQuantity(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="adj-form-reason">{t("inventory.adjust.reason")}</Label>
        <select id="adj-form-reason" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={reason} onChange={(e) => setReason(e.target.value)}>
          {REASONS.map((r) => <option key={r} value={r}>{t(`inventory.reasons.${r}`)}</option>)}
        </select>
      </div>
      <div className="space-y-1">
        <Label htmlFor="adj-form-note">{t("inventory.adjust.note")}</Label>
        <Textarea dictation={{ context: "inventory" }} id="adj-form-note" value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={pending || !valid}>{t("approvals.edit.save")}</Button>
        <Button type="button" variant="outline" onClick={onCancel}>{t("common.cancel")}</Button>
      </div>
    </form>
  )
}
