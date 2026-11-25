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
import { useAddSupplierCredit } from "./billHooks"
import { BILL_PAYMENT_METHODS, creditableOf, supplierRefundFor, type Bill } from "./billModel"

const DATE = /^\d{4}-\d{2}-\d{2}$/

/**
 * Records a credit the supplier gave against a bill. It takes off what is still owed first; whatever is beyond that the
 * supplier must have paid back, and the dialog asks how.
 */
export function SupplierCreditDialog({ bill, onClose, onConflict }: { bill: Bill; onClose: () => void; onConflict: () => void }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  // Freeze what the dialog opened with, so a background refetch cannot pair the form with a newer version.
  const [opened] = useState(() => bill)
  const creditable = creditableOf(opened)
  const today = formatInZone(useNow(60_000).toISOString(), businessTimeZone, "yyyy-MM-dd")
  const [amount, setAmount] = useState(() => minorToDecimal(opened.balance_minor > 0 ? opened.balance_minor : creditable, opened.currency))
  const [creditDate, setCreditDate] = useState(today)
  const [reference, setReference] = useState("")
  const [reason, setReason] = useState("")
  const [receivedOn, setReceivedOn] = useState(today)
  const [method, setMethod] = useState<(typeof BILL_PAYMENT_METHODS)[number]>("bank_transfer")
  const [refundReference, setRefundReference] = useState("")
  const add = useAddSupplierCredit(opened.id)
  const money = (minor: number) => formatMoney({ amount_minor: minor, currency: opened.currency })

  const parsed = parseMoneyInput(amount, opened.currency)
  const amountError = !parsed.ok ? t(`sales.priceErrors.${parsed.error}`, { currency: opened.currency, digits: parsed.digits })
    : parsed.minor <= 0 ? t("bills.errors.zero")
      : parsed.minor > creditable ? t("bills.credits.errors.overCreditable", { creditable: money(creditable) })
        : null
  const credit = parsed.ok && !amountError ? parsed.minor : 0
  const refund = supplierRefundFor(opened, credit)
  const dateError = !DATE.test(creditDate) ? t("bills.errors.date") : creditDate > today ? t("bills.credits.errors.future") : null
  const refundDateError = refund === 0 ? null : !DATE.test(receivedOn) ? t("bills.errors.date") : receivedOn > today ? t("bills.credits.errors.futureRefund") : null
  const ready = credit > 0 && !dateError && !refundDateError && !add.isPending
  const submit = () => {
    if (!ready) return
    add.mutateAsync({
      version: opened.version, amount_minor: credit, credit_date: creditDate,
      supplier_reference: reference.trim() || null, reason: reason.trim() || null,
      refund: refund > 0 ? { received_on: receivedOn, method, reference: refundReference.trim() || null } : null,
    }).then(
      () => { toast.success(t("bills.credits.recorded")); onClose() },
      (err: unknown) => { if (err instanceof ApiProblem && err.isConflict && err.code.endsWith("version_conflict")) { onClose(); onConflict() } },
    )
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !add.isPending) onClose() }}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{t("bills.credits.record")}</DialogTitle>
        <DialogDescription>{t("bills.credits.hint", { supplier: opened.supplier_name, balance: money(opened.balance_minor), creditable: money(creditable) })}</DialogDescription>
      </DialogHeader>
      <form id="supplier-credit" className="space-y-3" noValidate onSubmit={(e) => { e.preventDefault(); submit() }}>
        <div className="space-y-1">
          <Label htmlFor="credit-amount">{t("bills.credits.amount", { currency: opened.currency })}</Label>
          <Input id="credit-amount" inputMode="decimal" value={amount} aria-invalid={!!amountError} aria-describedby={amountError ? "credit-amount-error" : undefined} disabled={add.isPending} onChange={(e) => setAmount(e.target.value)} />
          {amountError ? <p id="credit-amount-error" className="text-sm text-destructive">{amountError}</p> : null}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="credit-date">{t("bills.credits.date")}</Label>
            <Input id="credit-date" type="date" value={creditDate} max={today} aria-invalid={!!dateError} aria-describedby={dateError ? "credit-date-error" : undefined} disabled={add.isPending} onChange={(e) => setCreditDate(e.target.value)} />
            {dateError ? <p id="credit-date-error" className="text-sm text-destructive">{dateError}</p> : null}
          </div>
          <div className="space-y-1">
            <Label htmlFor="credit-reference">{t("bills.credits.reference")}</Label>
            <Input id="credit-reference" value={reference} maxLength={100} disabled={add.isPending} onChange={(e) => setReference(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="credit-reason">{t("bills.credits.reason")}</Label>
          <Textarea id="credit-reason" value={reason} maxLength={500} disabled={add.isPending} onChange={(e) => setReason(e.target.value)} />
        </div>
        <dl className="ml-auto grid max-w-sm grid-cols-2 gap-1 border-t pt-3 text-sm">
          <dt>{t("bills.credits.againstBalance")}</dt><dd className="text-right">{money(credit - refund)}</dd>
          <dt>{t("bills.credits.refund")}</dt><dd className="text-right">{money(refund)}</dd>
        </dl>
        {refund > 0 ? <fieldset className="space-y-3 rounded-md border p-3">
          <legend className="px-1 text-sm font-medium">{t("bills.credits.refundDetails")}</legend>
          <p className="text-sm">{t("bills.credits.refundHint", { amount: money(refund) })}</p>
          <div className="space-y-1">
            <Label htmlFor="credit-refund-date">{t("bills.credits.refundDate")}</Label>
            <Input id="credit-refund-date" type="date" value={receivedOn} max={today} aria-invalid={!!refundDateError} aria-describedby={refundDateError ? "credit-refund-date-error" : undefined} disabled={add.isPending} onChange={(e) => setReceivedOn(e.target.value)} />
            {refundDateError ? <p id="credit-refund-date-error" className="text-sm text-destructive">{refundDateError}</p> : null}
          </div>
          <div className="space-y-1">
            <Label htmlFor="credit-refund-method">{t("bills.payments.method")}</Label>
            <select id="credit-refund-method" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={method} disabled={add.isPending} onChange={(e) => setMethod(e.target.value as (typeof BILL_PAYMENT_METHODS)[number])}>
              {BILL_PAYMENT_METHODS.map((m) => <option key={m} value={m}>{t(`sales.payments.methods.${m}`)}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="credit-refund-reference">{t("bills.payments.reference")}</Label>
            <Input id="credit-refund-reference" value={refundReference} maxLength={200} disabled={add.isPending} onChange={(e) => setRefundReference(e.target.value)} />
          </div>
        </fieldset> : null}
        {add.error ? <p role="alert" className="text-sm text-destructive">{describeError(add.error)}</p> : null}
      </form>
      <DialogFooter>
        <Button variant="outline" disabled={add.isPending} onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" form="supplier-credit" disabled={!ready}>{t("bills.credits.record")}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
