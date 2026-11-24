import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { formatQuantity as qty } from "@/lib/quantity"
import { toastError } from "@/lib/errors"
import { useCountStock } from "../hooks"

/** Record what is physically on the shelf. The server books the difference as a count correction. */
export function CountDialog({ itemId, itemName, onHand, onOpenChange }: { itemId: string; itemName: string; onHand: string; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const count = useCountStock()
  const [counted, setCounted] = useState("")
  const [note, setNote] = useState("")
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    const n = Number(counted)
    if (counted.trim() === "" || !Number.isFinite(n) || n < 0) return setError(t("inventory.count.invalid"))
    count.mutate({ item_id: itemId, counted: counted.trim(), note: note.trim() || null }, {
      onSuccess: (movement) => {
        toast.success(movement ? t("inventory.count.corrected", { change: `${Number(movement.quantity) > 0 ? "+" : ""}${qty(movement.quantity)}` }) : t("inventory.count.matched"))
        onOpenChange(false)
      },
      onError: (e) => toastError(e),
    })
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("inventory.count.title", { name: itemName })}</DialogTitle>
          <DialogDescription>{t("inventory.count.hint", { onHand: qty(onHand) })}</DialogDescription>
        </DialogHeader>
        <form id="count-form" className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <div className="space-y-1">
            <Label htmlFor="count-qty">{t("inventory.count.counted")}</Label>
            <Input id="count-qty" inputMode="decimal" value={counted} onChange={(e) => setCounted(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="count-note">{t("inventory.adjust.note")}</Label>
            <Input id="count-note" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          <Button type="submit" form="count-form" disabled={count.isPending}>{t("inventory.count.submit")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
