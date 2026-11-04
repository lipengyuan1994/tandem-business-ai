import { QueryError } from "@/components/common/QueryError"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { ConflictDialog } from "@/components/common/ConflictDialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { describeError, toastError } from "@/lib/errors"
import { minorToDecimal, parseMoneyInput } from "@/lib/money"
import { FieldError } from "./FieldError"
import { fieldInvalid } from "./validation"
import { usePurchasingPickers, useSaveSupplier, useSuppliers } from "./hooks"
import type { Supplier } from "./model"

interface ItemDraft { item: string; sku: string; price: string; days: string }
export function SupplierDialog({ supplier, onClose }: { supplier?: Supplier; onClose: () => void }) {
  const { t } = useTranslation()
  const pickers = usePurchasingPickers()
  const suppliers = useSuppliers()
  const save = useSaveSupplier(supplier?.id)
  const [name, setName] = useState(supplier?.name ?? "")
  const [email, setEmail] = useState(supplier?.email ?? "")
  const [phone, setPhone] = useState(supplier?.phone ?? "")
  const [notes, setNotes] = useState(supplier?.notes ?? "")
  const [version, setVersion] = useState(supplier?.version)
  const [conflict, setConflict] = useState(false)
  const currency = pickers.data?.currency
  // Mount this dialog only after pickers load, so existing prices use the business currency.
  const [items, setItems] = useState<ItemDraft[]>(() => supplier?.items?.map((i) => ({ item: i.catalog_item_id, sku: i.supplier_sku ?? "", price: minorToDecimal(i.last_price_minor, currency!), days: String(i.lead_time_days ?? 0) })) ?? [])
  const change = (index: number, patch: Partial<ItemDraft>) => setItems(items.map((item, i) => i === index ? { ...item, ...patch } : item))
  const ready = name.trim() && currency && new Set(items.map((i) => i.item)).size === items.length && items.every((i) => {
    const p = parseMoneyInput(i.price, currency)
    return i.item && p.ok && p.minor <= 10 ** 12 && /^\d+$/.test(i.days) && Number(i.days) <= 365
  }) && !pickers.error && !conflict && !save.isPending
  return <Dialog open onOpenChange={(open) => { if (!open) onClose() }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
    <DialogHeader><DialogTitle>{t(supplier ? "purchasing.editSupplier" : "purchasing.newSupplier")}</DialogTitle><DialogDescription>{t("purchasing.supplierHint")}</DialogDescription></DialogHeader>
    {pickers.error ? <QueryError error={pickers.error} onRetry={() => pickers.refetch()} /> : null}
    <form id="supplier-form" className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (!ready || !currency) return
      save.mutate({ name: name.trim(), email: email || null, phone: phone || null, notes: notes || null, items: items.map((i) => { const p = parseMoneyInput(i.price, currency); if (!p.ok) throw new Error("Invalid price"); return { catalog_item_id: i.item, supplier_sku: i.sku || null, last_price_minor: p.minor, lead_time_days: Number(i.days) } }), ...(version ? { version } : {}) }, { onSuccess: () => { toast.success(t("common.saved")); onClose() }, onError: (e) => toastError(e, () => setConflict(true)) })
    }}>
      <div><Label htmlFor="supplier-name">{t("purchasing.name")}</Label><Input id="supplier-name" aria-invalid={fieldInvalid(save.error, "name")} required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /><FieldError error={save.error} field="name" /></div>
      <div><Label htmlFor="supplier-email">{t("purchasing.email")}</Label><Input id="supplier-email" aria-invalid={fieldInvalid(save.error, "email")} type="email" value={email} onChange={(e) => setEmail(e.target.value)} /><FieldError error={save.error} field="email" /></div>
      <div><Label htmlFor="supplier-phone">{t("purchasing.phone")}</Label><Input id="supplier-phone" aria-invalid={fieldInvalid(save.error, "phone")} value={phone} onChange={(e) => setPhone(e.target.value)} /><FieldError error={save.error} field="phone" /></div>
      <div><Label htmlFor="supplier-notes">{t("purchasing.notes")}</Label><Textarea id="supplier-notes" aria-invalid={fieldInvalid(save.error, "notes")} value={notes} onChange={(e) => setNotes(e.target.value)} /><FieldError error={save.error} field="notes" /></div>
      {items.map((item, i) => <fieldset key={i} className="grid gap-3 rounded-md border p-3 sm:grid-cols-2"><legend>{t("purchasing.line", { n: i + 1 })}</legend>
        <div><Label htmlFor={`si-item-${i}`}>{t("purchasing.item")}</Label><select id={`si-item-${i}`} aria-invalid={fieldInvalid(save.error, `items.${i}.catalog_item_id`)} className="h-9 w-full rounded-md border bg-background px-2" value={item.item} onChange={(e) => change(i, { item: e.target.value })}>
          <option value="">{t("purchasing.chooseItem")}</option>{pickers.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select><FieldError error={save.error} field={`items.${i}.catalog_item_id`} /></div>
        <div><Label htmlFor={`si-sku-${i}`}>{t("purchasing.sku")}</Label><Input id={`si-sku-${i}`} aria-invalid={fieldInvalid(save.error, `items.${i}.supplier_sku`)} value={item.sku} onChange={(e) => change(i, { sku: e.target.value })} /><FieldError error={save.error} field={`items.${i}.supplier_sku`} /></div>
        <div><Label htmlFor={`si-price-${i}`}>{t("purchasing.lastPrice", { currency })}</Label><Input id={`si-price-${i}`} aria-invalid={fieldInvalid(save.error, `items.${i}.last_price_minor`)} inputMode="decimal" value={item.price} onChange={(e) => change(i, { price: e.target.value })} /><FieldError error={save.error} field={`items.${i}.last_price_minor`} /></div>
        <div><Label htmlFor={`si-days-${i}`}>{t("purchasing.leadTime")}</Label><Input id={`si-days-${i}`} aria-invalid={fieldInvalid(save.error, `items.${i}.lead_time_days`)} inputMode="numeric" value={item.days} onChange={(e) => change(i, { days: e.target.value })} /><FieldError error={save.error} field={`items.${i}.lead_time_days`} /></div>
        <Button type="button" variant="outline" onClick={() => setItems(items.filter((_, index) => index !== i))}>{t("purchasing.removeLine")}</Button>
      </fieldset>)}
      <Button type="button" variant="outline" disabled={items.length >= 200} onClick={() => setItems([...items, { item: "", sku: "", price: "", days: "0" }])}>{t("purchasing.addItem")}</Button>
      {save.error ? <p role="alert" className="text-sm text-destructive">{describeError(save.error)}</p> : null}
    </form>
    <DialogFooter><Button variant="outline" onClick={onClose}>{t("common.cancel")}</Button><Button form="supplier-form" disabled={!ready}>{t("common.save")}</Button></DialogFooter>
    <ConflictDialog open={conflict} onOpenChange={setConflict} onReload={() => { void suppliers.refetch().then((r) => { if (r.error) { toastError(r.error); return } const fresh = r.data?.find((s) => s.id === supplier?.id); if (fresh) { setVersion(fresh.version); setConflict(false) } }) }} />
  </DialogContent></Dialog>
}
