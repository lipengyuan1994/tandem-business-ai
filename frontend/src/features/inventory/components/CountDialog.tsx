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

type Level = { stock_location_id: string; on_hand: string }

/**
 * Record what is physically on the shelf at one stock location. The server books the difference as a count
 * correction at that location, so the balance shown and the location sent must match.
 */
export function CountDialog({ itemId, itemName, levels, onOpenChange }: { itemId: string; itemName: string; levels: Level[]; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const count = useCountStock()
  const [locationId, setLocationId] = useState(levels[0]?.stock_location_id ?? "")
  const onHand = levels.find((l) => l.stock_location_id === locationId)?.on_hand ?? "0"
  const [counted, setCounted] = useState("")
  const [note, setNote] = useState("")
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    if (!/^\d+(?:\.\d{1,3})?$/.test(counted.trim())) return setError(t("inventory.count.invalid"))
    count.mutate({ item_id: itemId, stock_location_id: locationId || null, counted: counted.trim(), note: note.trim() || null }, {
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
          {levels.length > 1 ? (
            <div className="space-y-1">
              <Label htmlFor="count-location">{t("inventory.count.location")}</Label>
              <select id="count-location" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={locationId} onChange={(e) => setLocationId(e.target.value)}>
                {levels.map((l, i) => <option key={l.stock_location_id} value={l.stock_location_id}>{t("inventory.count.locationOption", { n: i + 1, onHand: qty(l.on_hand) })}</option>)}
              </select>
            </div>
          ) : null}
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
