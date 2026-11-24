import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { CatalogItem } from "@/api/types"
import { QueryError } from "@/components/common/QueryError"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { toastError } from "@/lib/errors"
import { quantityInput } from "@/lib/quantity"
import { useCatalogItems, useReplaceParts, useServiceParts } from "./hooks"

interface Line { item_id: string; item_name: string; quantity: string }
const validQty = (q: string) => /^\d+(?:\.\d{1,3})?$/.test(q.trim()) && Number(q) > 0

function PartsEditor({ service, initial, onOpenChange }: { service: CatalogItem; initial: Line[]; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const products = useCatalogItems("product")
  const replace = useReplaceParts(service.id)
  const [lines, setLines] = useState(initial)
  const [adding, setAdding] = useState("")
  const choices = (products.data?.items ?? []).filter((p) => p.status === "active" && p.is_stock_tracked && !lines.some((l) => l.item_id === p.id))
  const invalid = lines.some((l) => !validQty(l.quantity))

  const add = () => {
    const p = choices.find((c) => c.id === adding)
    if (!p) return
    setLines([...lines, { item_id: p.id, item_name: p.name, quantity: "1" }])
    setAdding("")
  }
  const save = () => replace.mutate(lines.map((l) => ({ item_id: l.item_id, quantity: l.quantity.trim() })), {
    onSuccess: () => { toast.success(t("catalog.parts.saved")); onOpenChange(false) },
    onError: (e) => toastError(e),
  })

  return (
    <>
      {lines.length === 0 ? <p className="text-sm text-muted-foreground">{t("catalog.parts.none")}</p> : (
        <ul className="space-y-2" aria-label={t("catalog.parts.title", { name: service.name })}>
          {lines.map((l, i) => (
            <li key={l.item_id} className="flex items-center gap-2">
              <span className="flex-1 text-sm">{l.item_name}</span>
              <Input className="w-24" inputMode="decimal" aria-label={t("catalog.parts.quantityFor", { name: l.item_name })} aria-invalid={!validQty(l.quantity)}
                value={l.quantity} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
              <Button type="button" size="sm" variant="ghost" aria-label={t("catalog.parts.removeFor", { name: l.item_name })} onClick={() => setLines(lines.filter((_, j) => j !== i))}>{t("catalog.parts.remove")}</Button>
            </li>
          ))}
        </ul>
      )}
      {invalid ? <p role="alert" className="text-sm text-destructive">{t("catalog.parts.invalidQuantity")}</p> : null}
      {products.error ? <QueryError error={products.error} onRetry={() => products.refetch()} /> : (
        <div className="flex items-end gap-2">
          <div className="min-w-0 flex-1 space-y-1">
            <Label htmlFor="part-add">{t("catalog.parts.add")}</Label>
            <select id="part-add" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={adding} onChange={(e) => setAdding(e.target.value)}>
              <option value="">{choices.length ? t("catalog.parts.choose") : t("catalog.parts.noProducts")}</option>
              {choices.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <Button type="button" variant="outline" disabled={!adding} onClick={add}>{t("catalog.parts.addButton")}</Button>
        </div>
      )}
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
        <Button disabled={replace.isPending || invalid} onClick={save}>{t("common.save")}</Button>
      </DialogFooter>
    </>
  )
}

/** Stock items a service uses up (a brake service uses one pair of pads). Booking reserves them; completing consumes them. */
export function PartsDialog({ service, onOpenChange }: { service: CatalogItem; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const parts = useServiceParts(service.id)
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("catalog.parts.title", { name: service.name })}</DialogTitle>
          <DialogDescription>{t("catalog.parts.hint")}</DialogDescription>
        </DialogHeader>
        {parts.error ? <QueryError error={parts.error} onRetry={() => parts.refetch()} />
          : parts.isPending ? <Skeleton className="h-16 w-full" />
          : <PartsEditor service={service} initial={parts.data.map((p) => ({ item_id: p.item_id, item_name: p.item_name, quantity: quantityInput(p.quantity) }))} onOpenChange={onOpenChange} />}
      </DialogContent>
    </Dialog>
  )
}
