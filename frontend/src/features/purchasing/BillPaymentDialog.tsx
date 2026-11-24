import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { formatInZone } from "@/lib/dates"
import { describeError } from "@/lib/errors"
import { formatMoney, minorToDecimal, parseMoneyInput } from "@/lib/money"
import { ApiProblem } from "@/lib/problem"
import { useNow } from "@/lib/use-now"
import { useRecordBillPayment } from "./billHooks"
import { BILL_PAYMENT_METHODS, type Bill } from "./billModel"

/** Records a full or partial payment to a supplier. The amount starts at the open balance and may not exceed it. */
export function BillPaymentDialog({ bill, onClose, onConflict }: { bill: Bill; onClose: () => void; onConflict: () => void }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  // Freeze what the dialog opened with, so a background refetch cannot pair the form with a newer version.
  const [{ version, balance }] = useState(() => ({ version: bill.version, balance: bill.balance_minor }))
  const today = formatInZone(useNow(60_000).toISOString(), businessTimeZone, "yyyy-MM-dd")
  const [amount, setAmount] = useState(() => minorToDecimal(balance, bill.currency))
  const [paidOn, setPaidOn] = useState(today)
  const [method, setMethod] = useState<(typeof BILL_PAYMENT_METHODS)[number]>("bank_transfer")
  const [reference, setReference] = useState("")
  const record = useRecordBillPayment(bill.id)
  const parsed = parseMoneyInput(amount, bill.currency)
  const amountError = !parsed.ok ? t(`sales.priceErrors.${parsed.error}`, { currency: bill.currency, digits: parsed.digits })
    : parsed.minor <= 0 ? t("bills.errors.zero")
      : parsed.minor > balance ? t("bills.errors.overBalance", { balance: formatMoney({ amount_minor: balance, currency: bill.currency }) })
        : null
  const dateError = !/^\d{4}-\d{2}-\d{2}$/.test(paidOn) ? t("bills.errors.date") : paidOn > today ? t("bills.errors.futurePayment") : null
  const ready = parsed.ok && !amountError && !dateError && !record.isPending
  const submit = () => {
    if (!ready || !parsed.ok) return
    record.mutateAsync({ version, amount_minor: parsed.minor, paid_on: paidOn, method, reference: reference.trim() || null }).then(
      () => { toast.success(t("bills.payments.recorded")); onClose() },
      (err: unknown) => { if (err instanceof ApiProblem && err.isConflict && err.code.endsWith("version_conflict")) { onClose(); onConflict() } },
    )
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !record.isPending) onClose() }}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{t("bills.payments.record")}</DialogTitle>
        <DialogDescription>{t("bills.payments.hint", { supplier: bill.supplier_name, balance: formatMoney({ amount_minor: balance, currency: bill.currency }) })}</DialogDescription>
      </DialogHeader>
      <form id="bill-payment" className="space-y-3" noValidate onSubmit={(e) => { e.preventDefault(); submit() }}>
        <div className="space-y-1">
          <Label htmlFor="bill-payment-amount">{t("bills.payments.amount", { currency: bill.currency })}</Label>
          <Input id="bill-payment-amount" inputMode="decimal" value={amount} aria-invalid={!!amountError} aria-describedby={amountError ? "bill-payment-amount-error" : undefined} disabled={record.isPending} onChange={(e) => setAmount(e.target.value)} />
          {amountError ? <p id="bill-payment-amount-error" className="text-sm text-destructive">{amountError}</p> : null}
        </div>
        <div className="space-y-1">
          <Label htmlFor="bill-payment-date">{t("bills.payments.date")}</Label>
          <Input id="bill-payment-date" type="date" value={paidOn} max={today} aria-invalid={!!dateError} aria-describedby={dateError ? "bill-payment-date-error" : undefined} disabled={record.isPending} onChange={(e) => setPaidOn(e.target.value)} />
          {dateError ? <p id="bill-payment-date-error" className="text-sm text-destructive">{dateError}</p> : null}
        </div>
        <div className="space-y-1">
          <Label htmlFor="bill-payment-method">{t("bills.payments.method")}</Label>
          <select id="bill-payment-method" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={method} disabled={record.isPending} onChange={(e) => setMethod(e.target.value as (typeof BILL_PAYMENT_METHODS)[number])}>
            {BILL_PAYMENT_METHODS.map((m) => <option key={m} value={m}>{t(`sales.payments.methods.${m}`)}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="bill-payment-reference">{t("bills.payments.reference")}</Label>
          <Input id="bill-payment-reference" value={reference} maxLength={200} disabled={record.isPending} onChange={(e) => setReference(e.target.value)} />
        </div>
        {record.error ? <p role="alert" className="text-sm text-destructive">{describeError(record.error)}</p> : null}
      </form>
      <DialogFooter>
        <Button variant="outline" disabled={record.isPending} onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" form="bill-payment" disabled={!ready}>{t("bills.payments.record")}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
