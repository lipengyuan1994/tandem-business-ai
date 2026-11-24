import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { formatInZone } from "@/lib/dates"
import { describeError } from "@/lib/errors"
import { formatMoney, minorToDecimal, parseMoneyInput } from "@/lib/money"
import { ApiProblem } from "@/lib/problem"
import { useNow } from "@/lib/use-now"
import { useSaveBill, usePOBilling } from "./billHooks"
import { BILLABLE_PO_STATUSES, type Bill } from "./billModel"
import { useOrders, usePurchasingPickers, useSuppliers } from "./hooks"

const DATE = /^\d{4}-\d{2}-\d{2}$/

/**
 * Records a supplier bill, or edits an open one with no payments. Opened from a purchase order, the supplier and the
 * order are fixed and the total starts at the received value not yet billed.
 */
export function BillDialog({ bill, purchaseOrder, onClose, onSaved, onConflict }: {
  bill?: Bill
  purchaseOrder?: { id: string; supplierId: string; number: string | null }
  onClose: () => void
  onSaved?: (bill: Bill) => void
  onConflict?: () => void
}) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const today = formatInZone(useNow(60_000).toISOString(), businessTimeZone, "yyyy-MM-dd")
  const suppliers = useSuppliers()
  const orders = useOrders()
  const pickers = usePurchasingPickers()
  const [supplierId, setSupplierId] = useState(bill?.supplier_id ?? purchaseOrder?.supplierId ?? "")
  const [poId, setPoId] = useState(bill?.purchase_order_id ?? purchaseOrder?.id ?? "")
  const [reference, setReference] = useState(bill?.supplier_reference ?? "")
  const [billDate, setBillDate] = useState(bill?.bill_date ?? today)
  const [dueDate, setDueDate] = useState(bill?.due_date ?? "")
  const [notes, setNotes] = useState(bill?.notes ?? "")
  const billing = usePOBilling(bill ? undefined : poId || undefined)
  const currency = bill?.currency ?? billing.data?.currency ?? pickers.data?.currency ?? "USD"
  // Until the user types an amount, a new bill for an order starts at what was received and not yet billed.
  const [typed, setTyped] = useState<string | null>(bill ? minorToDecimal(bill.total_minor, bill.currency) : null)
  const unbilled = billing.data?.unbilled_received_minor ?? 0
  const amount = typed ?? (billing.data && unbilled > 0 ? minorToDecimal(unbilled, billing.data.currency) : "")
  const touchedAmount = typed !== null
  const save = useSaveBill(bill?.id)

  const fixedSupplier = !!bill || !!purchaseOrder
  const billableOrders = (orders.data?.pages.flatMap((p) => p.items) ?? []).filter((o) => o.supplier_id === supplierId && BILLABLE_PO_STATUSES.includes(o.status))
  const parsed = parseMoneyInput(amount, currency)
  const amountError = !parsed.ok ? t(`sales.priceErrors.${parsed.error}`, { currency, digits: parsed.digits })
    : parsed.minor <= 0 ? t("bills.errors.zero") : parsed.minor > 10 ** 14 ? t("sales.priceErrors.tooLarge") : null
  const billDateError = !DATE.test(billDate) ? t("bills.errors.date") : billDate > today ? t("bills.errors.future") : null
  const dueDateError = dueDate && !DATE.test(dueDate) ? t("bills.errors.date") : dueDate && dueDate < billDate ? t("bills.errors.dueBeforeBill") : null
  const ready = !!supplierId && parsed.ok && !amountError && !billDateError && !dueDateError && !save.isPending
  const submit = () => {
    if (!ready || !parsed.ok) return
    save.mutateAsync({
      version: bill?.version, supplier_id: supplierId, purchase_order_id: poId || null, supplier_reference: reference.trim() || null,
      bill_date: billDate, due_date: dueDate || null, total_minor: parsed.minor, notes: notes.trim() || null,
    }).then(
      (saved) => { toast.success(t(bill ? "common.saved" : "bills.recorded")); onClose(); onSaved?.(saved) },
      (err: unknown) => { if (err instanceof ApiProblem && err.isConflict && err.code.endsWith("version_conflict")) { onClose(); onConflict?.() } },
    )
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !save.isPending) onClose() }}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{t(bill ? "bills.edit" : "bills.new")}</DialogTitle>
        <DialogDescription>{t(bill ? "bills.editHint" : "bills.newHint")}</DialogDescription>
      </DialogHeader>
      <form id="supplier-bill" className="space-y-3" noValidate onSubmit={(e) => { e.preventDefault(); submit() }}>
        <div className="space-y-1">
          <Label htmlFor="bill-supplier">{t("purchasing.supplier")}</Label>
          <select id="bill-supplier" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={supplierId} disabled={fixedSupplier || save.isPending}
            onChange={(e) => { setSupplierId(e.target.value); setPoId("") }}>
            <option value="">{t("bills.chooseSupplier")}</option>
            {(suppliers.data ?? []).filter((s) => s.active || s.id === supplierId).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            {bill && !(suppliers.data ?? []).some((s) => s.id === bill.supplier_id) ? <option value={bill.supplier_id}>{bill.supplier_name}</option> : null}
          </select>
        </div>
        {!bill ? <div className="space-y-1">
          <Label htmlFor="bill-po">{t("bills.purchaseOrder")}</Label>
          <select id="bill-po" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={poId} disabled={!!purchaseOrder || !supplierId || save.isPending}
            onChange={(e) => { setPoId(e.target.value); setTyped(null) }}>
            <option value="">{t("bills.noPurchaseOrder")}</option>
            {purchaseOrder ? <option value={purchaseOrder.id}>{purchaseOrder.number ?? t("status.draft")}</option>
              : billableOrders.map((o) => <option key={o.id} value={o.id}>{o.number ?? t("status.draft")}</option>)}
          </select>
          {billing.data ? <p className="text-sm text-muted-foreground">{t("bills.poBillingHint", {
            received: formatMoney({ amount_minor: billing.data.received_minor, currency: billing.data.currency }),
            billed: formatMoney({ amount_minor: billing.data.billed_minor, currency: billing.data.currency }),
            ordered: formatMoney({ amount_minor: billing.data.ordered_minor, currency: billing.data.currency }),
          })}</p> : null}
        </div> : null}
        <div className="space-y-1">
          <Label htmlFor="bill-reference">{t("bills.reference")}</Label>
          <Input id="bill-reference" value={reference} maxLength={100} disabled={save.isPending} onChange={(e) => setReference(e.target.value)} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="bill-date">{t("bills.billDate")}</Label>
            <Input id="bill-date" type="date" value={billDate} max={today} aria-invalid={!!billDateError} aria-describedby={billDateError ? "bill-date-error" : undefined} disabled={save.isPending} onChange={(e) => setBillDate(e.target.value)} />
            {billDateError ? <p id="bill-date-error" className="text-sm text-destructive">{billDateError}</p> : null}
          </div>
          <div className="space-y-1">
            <Label htmlFor="bill-due">{t("bills.dueDate")}</Label>
            <Input id="bill-due" type="date" value={dueDate} min={billDate} aria-invalid={!!dueDateError} aria-describedby={dueDateError ? "bill-due-error" : undefined} disabled={save.isPending} onChange={(e) => setDueDate(e.target.value)} />
            {dueDateError ? <p id="bill-due-error" className="text-sm text-destructive">{dueDateError}</p> : null}
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="bill-total">{t("bills.total", { currency })}</Label>
          <Input id="bill-total" inputMode="decimal" value={amount} aria-invalid={touchedAmount && !!amountError} aria-describedby={touchedAmount && amountError ? "bill-total-error" : undefined} disabled={save.isPending}
            onChange={(e) => setTyped(e.target.value)} />
          {touchedAmount && amountError ? <p id="bill-total-error" className="text-sm text-destructive">{amountError}</p> : null}
        </div>
        <div className="space-y-1">
          <Label htmlFor="bill-notes">{t("bills.notes")}</Label>
          <Textarea id="bill-notes" value={notes} maxLength={4000} disabled={save.isPending} onChange={(e) => setNotes(e.target.value)} />
        </div>
        {save.error ? <p role="alert" className="text-sm text-destructive">{describeError(save.error)}</p> : null}
      </form>
      <DialogFooter>
        <Button variant="outline" disabled={save.isPending} onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" form="supplier-bill" disabled={!ready}>{t(bill ? "common.save" : "bills.record")}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
