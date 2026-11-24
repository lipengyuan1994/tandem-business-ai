import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { SalesDocument } from "@/api/types"
import { useSession } from "@/app/session-context"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { formatInZone } from "@/lib/dates"
import { describeError } from "@/lib/errors"
import { ApiProblem } from "@/lib/problem"
import { formatMoney, minorToDecimal, parseMoneyInput } from "@/lib/money"
import { useNow } from "@/lib/use-now"
import { useRecordPayment } from "../hooks"
import { PAYMENT_METHODS } from "../model"

/** Records a full or partial payment. The amount starts at the open balance and may not exceed it. */
export function PaymentDialog({ document: d, onClose, onConflict }: { document: SalesDocument; onClose: () => void; onConflict: () => void }) {
  const { t } = useTranslation()
  const { businessTimeZone } = useSession()
  const balance = d.balance_minor ?? 0
  const now = useNow(60_000)
  const today = formatInZone(now.toISOString(), businessTimeZone, "yyyy-MM-dd")
  const [amount, setAmount] = useState(() => minorToDecimal(balance, d.currency))
  const [paidOn, setPaidOn] = useState(today)
  const [method, setMethod] = useState<(typeof PAYMENT_METHODS)[number]>("bank_transfer")
  const [reference, setReference] = useState("")
  const record = useRecordPayment(d.id)
  const parsed = parseMoneyInput(amount, d.currency)
  const amountError = !parsed.ok ? t(`sales.priceErrors.${parsed.error}`, { currency: d.currency, digits: parsed.digits })
    : parsed.minor <= 0 ? t("sales.payments.errors.zero")
      : parsed.minor > balance ? t("sales.payments.errors.overBalance", { balance: formatMoney({ amount_minor: balance, currency: d.currency }) })
        : null
  const dateError = !/^\d{4}-\d{2}-\d{2}$/.test(paidOn) ? t("sales.payments.errors.date") : paidOn > today ? t("sales.payments.errors.future") : null
  const ready = parsed.ok && !amountError && !dateError && !record.isPending
  const submit = () => {
    if (!ready || !parsed.ok) return
    record.mutateAsync({ version: d.version, amount_minor: parsed.minor, paid_on: paidOn, method, reference: reference.trim() || null }).then(
      () => { toast.success(t("sales.payments.recorded")); onClose() },
      (err: unknown) => { if (err instanceof ApiProblem && err.isConflict && err.code.endsWith("version_conflict")) { onClose(); onConflict() } },
    )
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !record.isPending) onClose() }}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{t("sales.payments.record")}</DialogTitle>
        <DialogDescription>{t("sales.payments.hint", { number: d.number, balance: formatMoney({ amount_minor: balance, currency: d.currency }) })}</DialogDescription>
      </DialogHeader>
      <form id="invoice-payment" className="space-y-3" noValidate onSubmit={(e) => { e.preventDefault(); submit() }}>
        <div className="space-y-1">
          <Label htmlFor="payment-amount">{t("sales.payments.amount", { currency: d.currency })}</Label>
          <Input id="payment-amount" inputMode="decimal" value={amount} aria-invalid={!!amountError} aria-describedby={amountError ? "payment-amount-error" : undefined} disabled={record.isPending} onChange={(e) => setAmount(e.target.value)} />
          {amountError ? <p id="payment-amount-error" className="text-sm text-destructive">{amountError}</p> : null}
        </div>
        <div className="space-y-1">
          <Label htmlFor="payment-date">{t("sales.payments.date")}</Label>
          <Input id="payment-date" type="date" value={paidOn} max={today} aria-invalid={!!dateError} aria-describedby={dateError ? "payment-date-error" : undefined} disabled={record.isPending} onChange={(e) => setPaidOn(e.target.value)} />
          {dateError ? <p id="payment-date-error" className="text-sm text-destructive">{dateError}</p> : null}
        </div>
        <div className="space-y-1">
          <Label htmlFor="payment-method">{t("sales.payments.method")}</Label>
          <select id="payment-method" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={method} disabled={record.isPending} onChange={(e) => setMethod(e.target.value as (typeof PAYMENT_METHODS)[number])}>
            {PAYMENT_METHODS.map((m) => <option key={m} value={m}>{t(`sales.payments.methods.${m}`)}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="payment-reference">{t("sales.payments.reference")}</Label>
          <Input id="payment-reference" value={reference} maxLength={200} disabled={record.isPending} onChange={(e) => setReference(e.target.value)} />
        </div>
        {record.error ? <p role="alert" className="text-sm text-destructive">{describeError(record.error)}</p> : null}
      </form>
      <DialogFooter>
        <Button variant="outline" disabled={record.isPending} onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" form="invoice-payment" disabled={!ready}>{t("sales.payments.record")}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
