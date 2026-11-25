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
import { quantityInput } from "@/lib/quantity"
import { useNow } from "@/lib/use-now"
import { useSaveBill, usePOBilling } from "./billHooks"
import { BILLABLE_PO_STATUSES, billLineAmount, type Bill, type BillLineInput } from "./billModel"
import { useOrder, useOrders, usePurchasingPickers, useSuppliers } from "./hooks"

const DATE = /^\d{4}-\d{2}-\d{2}$/

/** A bill line as typed. Links to a catalog item or an order line come only from "fill from order" or the saved bill. */
interface LineDraft { key: string; description: string; quantity: string; unitCost: string; catalogItemId: string | null; poLineId: string | null }
const newKey = () => crypto.randomUUID()

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
  // The version the form was filled from. A background refetch must not let a stale form overwrite someone else's edit.
  const [version] = useState(bill?.version)
  const billing = usePOBilling(bill ? undefined : poId || undefined)
  // Minor units depend on the currency, so a new bill waits for the order's or the business's currency before saving.
  const knownCurrency = bill?.currency ?? (poId ? billing.data?.currency : pickers.data?.currency)
  const currency = knownCurrency ?? pickers.data?.currency ?? "USD"
  // Until the user types an amount, a new bill for an order starts at what was received and not yet billed.
  const [typed, setTyped] = useState<string | null>(bill ? minorToDecimal(bill.total_minor, bill.currency) : null)
  const unbilled = billing.data?.unbilled_received_minor ?? 0
  const amount = typed ?? (billing.data && unbilled > 0 ? minorToDecimal(unbilled, billing.data.currency) : "")
  const touchedAmount = typed !== null
  const save = useSaveBill(bill?.id)
  const [lines, setLines] = useState<LineDraft[]>(() => (bill?.lines ?? []).map((l) => ({
    key: l.id, description: l.description, quantity: quantityInput(String(l.quantity)), unitCost: minorToDecimal(l.unit_cost_minor, bill!.currency),
    catalogItemId: l.catalog_item_id, poLineId: l.purchase_order_line_id,
  })))
  const order = useOrder(poId)

  const fixedSupplier = !!bill || !!purchaseOrder
  const billableOrders = (orders.data?.pages.flatMap((p) => p.items) ?? []).filter((o) => o.supplier_id === supplierId && BILLABLE_PO_STATUSES.includes(o.status))
  const lineRows = lines.map((l) => {
    const cost = parseMoneyInput(l.unitCost, currency)
    const lineAmount = cost.ok ? billLineAmount(l.quantity, cost.minor) : null
    const descriptionError = l.description.trim() ? null : t("bills.lines.errors.description")
    const quantityError = billLineAmount(l.quantity, 0) === null ? t("bills.lines.errors.quantity") : null
    const costError = !cost.ok ? t(`sales.priceErrors.${cost.error}`, { currency, digits: cost.digits }) : null
    return { l, cost, amount: lineAmount, descriptionError, quantityError, costError }
  })
  const itemised = lines.length > 0
  const linesTotal = lineRows.reduce((sum, r) => sum + (r.amount ?? 0), 0)
  const linesError = lineRows.some((r) => r.descriptionError || r.quantityError || r.costError)
  const linesTotalError = itemised && !linesError && linesTotal <= 0 ? t("bills.lines.errors.total") : null
  const parsed = itemised ? { ok: true as const, minor: linesTotal } : parseMoneyInput(amount, currency)
  const amountError = itemised ? null : !parsed.ok ? t(`sales.priceErrors.${parsed.error}`, { currency, digits: parsed.digits })
    : parsed.minor <= 0 ? t("bills.errors.zero") : parsed.minor > 10 ** 14 ? t("sales.priceErrors.tooLarge") : null
  const billDateError = !DATE.test(billDate) ? t("bills.errors.date") : billDate > today ? t("bills.errors.future") : null
  const dueDateError = dueDate && !DATE.test(dueDate) ? t("bills.errors.date") : dueDate && dueDate < billDate ? t("bills.errors.dueBeforeBill") : null
  const ready = !!supplierId && !!knownCurrency && parsed.ok && !amountError && !linesError && !linesTotalError && !billDateError && !dueDateError && !save.isPending
  const setLine = (key: string, change: Partial<LineDraft>) => setLines((ls) => ls.map((l) => l.key === key ? { ...l, ...change } : l))
  const orderLines = poId ? (order.data?.lines ?? []) : []
  const fillFromOrder = () => setLines(orderLines.map((l) => {
    const received = Number(l.received_quantity) > 0
    return {
      key: newKey(), description: l.name, quantity: quantityInput(String(received ? l.received_quantity : l.quantity)),
      unitCost: minorToDecimal(l.unit_cost_minor, currency), catalogItemId: l.catalog_item_id, poLineId: l.id,
    }
  }))
  const submit = () => {
    if (!ready || !parsed.ok) return
    save.mutateAsync({
      version, supplier_id: supplierId, purchase_order_id: poId || null, supplier_reference: reference.trim() || null,
      bill_date: billDate, due_date: dueDate || null, notes: notes.trim() || null,
      ...(itemised ? { total_minor: null, lines: lineRows.map(({ l, cost }): BillLineInput => ({
        description: l.description.trim(), quantity: l.quantity.trim(), unit_cost_minor: cost.ok ? cost.minor : 0,
        catalog_item_id: l.catalogItemId, purchase_order_line_id: l.poLineId,
      })) } : { total_minor: parsed.minor }),
    }).then(
      (saved) => { toast.success(t(bill ? "common.saved" : "bills.recorded")); onClose(); onSaved?.(saved) },
      (err: unknown) => { if (err instanceof ApiProblem && err.isConflict && err.code.endsWith("version_conflict")) { onClose(); onConflict?.() } },
    )
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !save.isPending) onClose() }}>
    <DialogContent className="max-w-2xl">
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
            onChange={(e) => { setPoId(e.target.value); setTyped(null); setLines((ls) => ls.map((l) => ({ ...l, poLineId: null }))) }}>
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
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{t("bills.lines.title")}</legend>
          {!itemised ? <p className="text-sm text-muted-foreground">{t("bills.lines.hint")}</p> : null}
          {lineRows.map(({ l, amount: lineAmount, descriptionError, quantityError, costError }, index) => {
            const n = index + 1
            const err = (id: string, text: string | null) => text ? <p id={id} className="text-sm text-destructive">{text}</p> : null
            return <div key={l.key} className="grid grid-cols-[1fr_5rem_6rem_5rem_auto] items-start gap-2">
              <div>
                <Input aria-label={t("bills.lines.description", { n })} value={l.description} maxLength={200} disabled={save.isPending} aria-invalid={!!descriptionError}
                  aria-describedby={descriptionError ? `bill-line-${l.key}-d` : undefined} onChange={(e) => setLine(l.key, { description: e.target.value })} />
                {err(`bill-line-${l.key}-d`, descriptionError)}
              </div>
              <div>
                <Input aria-label={t("bills.lines.quantity", { n })} inputMode="decimal" value={l.quantity} disabled={save.isPending} aria-invalid={!!quantityError}
                  aria-describedby={quantityError ? `bill-line-${l.key}-q` : undefined} onChange={(e) => setLine(l.key, { quantity: e.target.value })} />
                {err(`bill-line-${l.key}-q`, quantityError)}
              </div>
              <div>
                <Input aria-label={t("bills.lines.unitCost", { n, currency })} inputMode="decimal" value={l.unitCost} disabled={save.isPending} aria-invalid={!!costError}
                  aria-describedby={costError ? `bill-line-${l.key}-c` : undefined} onChange={(e) => setLine(l.key, { unitCost: e.target.value })} />
                {err(`bill-line-${l.key}-c`, costError)}
              </div>
              <p className="pt-2 text-right text-sm">{lineAmount === null ? "—" : formatMoney({ amount_minor: lineAmount, currency })}</p>
              <Button type="button" variant="ghost" size="sm" disabled={save.isPending} aria-label={t("bills.lines.remove", { n })}
                onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}>×</Button>
            </div>
          })}
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" disabled={save.isPending}
              onClick={() => setLines((ls) => [...ls, { key: newKey(), description: "", quantity: "1", unitCost: "", catalogItemId: null, poLineId: null }])}>{t("bills.lines.add")}</Button>
            {orderLines.length ? <Button type="button" variant="outline" size="sm" disabled={save.isPending} onClick={fillFromOrder}>{t("bills.lines.fromOrder")}</Button> : null}
          </div>
          {linesTotalError ? <p className="text-sm text-destructive">{linesTotalError}</p> : null}
        </fieldset>
        {itemised ? <p className="text-sm font-medium">{t("bills.lines.total", { total: formatMoney({ amount_minor: linesTotal, currency }) })}</p> : <div className="space-y-1">
          <Label htmlFor="bill-total">{t("bills.total", { currency })}</Label>
          <Input id="bill-total" inputMode="decimal" value={amount} aria-invalid={touchedAmount && !!amountError} aria-describedby={touchedAmount && amountError ? "bill-total-error" : undefined} disabled={save.isPending}
            onChange={(e) => setTyped(e.target.value)} />
          {touchedAmount && amountError ? <p id="bill-total-error" className="text-sm text-destructive">{amountError}</p> : null}
        </div>}
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
