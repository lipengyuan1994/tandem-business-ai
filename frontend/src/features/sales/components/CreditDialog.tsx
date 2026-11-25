import { useNavigate } from "@tanstack/react-router"
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
import { formatMoney } from "@/lib/money"
import { ApiProblem } from "@/lib/problem"
import { formatQuantity } from "@/lib/quantity"
import { useNow } from "@/lib/use-now"
import { creditableLines, creditAmount, milliText, quantityMilli, refundFor } from "../credit"
import { useCreditInvoice } from "../hooks"
import { PAYMENT_METHODS } from "../model"

/** Credits some or all of what is left on an invoice. Anything beyond the open balance is refunded to the customer. */
export function CreditDialog({ document: d, onClose, onConflict }: { document: SalesDocument; onClose: () => void; onConflict: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { businessTimeZone } = useSession()
  // Freeze what the dialog opened with, so a background refetch cannot pair the form with a newer version.
  const [{ version, lines }] = useState(() => ({ version: d.version, lines: creditableLines(d) }))
  const [quantities, setQuantities] = useState(() => Object.fromEntries(lines.map((c) => [c.line.id, milliText(c.leftMilli)])))
  const now = useNow(60_000)
  const today = formatInZone(now.toISOString(), businessTimeZone, "yyyy-MM-dd")
  const [paidOn, setPaidOn] = useState(today)
  const [method, setMethod] = useState<(typeof PAYMENT_METHODS)[number]>("bank_transfer")
  const [reference, setReference] = useState("")
  const credit = useCreditInvoice(d.id)
  const money = (amount: number) => formatMoney({ amount_minor: amount, currency: d.currency })

  const rows = lines.map((c) => {
    const text = quantities[c.line.id] ?? ""
    const milli = text.trim() === "" ? 0n : quantityMilli(text)
    const error = milli === null ? t("sales.credit.errors.quantity") : milli > c.leftMilli ? t("sales.credit.errors.overLeft", { left: formatQuantity(milliText(c.leftMilli)) }) : null
    return { c, milli: milli ?? 0n, error, amount: error ? 0 : creditAmount(c, milli ?? 0n) }
  })
  const total = rows.reduce((sum, r) => sum + r.amount, 0)
  const refund = refundFor(d, total)
  const lineError = rows.some((r) => r.error)
  const dateError = refund === 0 ? null : !/^\d{4}-\d{2}-\d{2}$/.test(paidOn) ? t("sales.payments.errors.date") : paidOn > today ? t("sales.credit.errors.future") : null
  const ready = !lineError && total > 0 && !dateError && !credit.isPending
  const submit = () => {
    if (!ready) return
    credit.mutateAsync({
      version,
      lines: rows.filter((r) => r.milli > 0n).map((r) => ({ line_id: r.c.line.id, quantity: milliText(r.milli) })),
      refund: refund > 0 ? { paid_on: paidOn, method, reference: reference.trim() || null } : null,
    }).then(
      (note) => {
        toast.success(t("sales.credit.done"))
        onClose()
        void navigate({ to: "/sales/invoices/$id", params: { id: note.id } })
      },
      (err: unknown) => { if (err instanceof ApiProblem && err.isConflict && err.code.endsWith("version_conflict")) { onClose(); onConflict() } },
    )
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !credit.isPending) onClose() }}>
    <DialogContent className="max-w-2xl">
      <DialogHeader>
        <DialogTitle>{t("sales.credit.title")}</DialogTitle>
        <DialogDescription>{t("sales.credit.hint", { number: d.number, balance: money(d.balance_minor ?? 0) })}</DialogDescription>
      </DialogHeader>
      <form id="invoice-credit" className="space-y-4" noValidate onSubmit={(e) => { e.preventDefault(); submit() }}>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{t("sales.credit.lines")}</legend>
          {rows.map(({ c, error, amount }) => <div key={c.line.id} className="grid grid-cols-[1fr_7rem_7rem] items-start gap-2">
            <div className="text-sm">
              <Label htmlFor={`credit-${c.line.id}`}>{t("sales.credit.quantityOf", { name: c.line.name })}</Label>
              <p className="text-muted-foreground">{t("sales.credit.left", { left: formatQuantity(milliText(c.leftMilli)) })}</p>
            </div>
            <div>
              <Input id={`credit-${c.line.id}`} inputMode="decimal" value={quantities[c.line.id] ?? ""} aria-invalid={!!error} aria-describedby={error ? `credit-${c.line.id}-error` : undefined} disabled={credit.isPending}
                onChange={(e) => setQuantities((q) => ({ ...q, [c.line.id]: e.target.value }))} />
              {error ? <p id={`credit-${c.line.id}-error`} className="text-sm text-destructive">{error}</p> : null}
            </div>
            <p className="pt-2 text-right text-sm">{money(amount)}</p>
          </div>)}
        </fieldset>
        <dl className="ml-auto grid max-w-sm grid-cols-2 gap-1 border-t pt-3 text-sm">
          <dt className="font-semibold">{t("sales.credit.total")}</dt><dd className="text-right font-semibold">{money(total)}</dd>
          <dt>{t("sales.credit.againstBalance")}</dt><dd className="text-right">{money(total - refund)}</dd>
          <dt>{t("sales.credit.refund")}</dt><dd className="text-right">{money(refund)}</dd>
        </dl>
        {refund > 0 ? <fieldset className="space-y-3 rounded-md border p-3">
          <legend className="px-1 text-sm font-medium">{t("sales.credit.refundDetails")}</legend>
          <p className="text-sm">{t("sales.credit.refundHint", { amount: money(refund) })}</p>
          <div className="space-y-1">
            <Label htmlFor="refund-date">{t("sales.credit.refundDate")}</Label>
            <Input id="refund-date" type="date" value={paidOn} max={today} aria-invalid={!!dateError} aria-describedby={dateError ? "refund-date-error" : undefined} disabled={credit.isPending} onChange={(e) => setPaidOn(e.target.value)} />
            {dateError ? <p id="refund-date-error" className="text-sm text-destructive">{dateError}</p> : null}
          </div>
          <div className="space-y-1">
            <Label htmlFor="refund-method">{t("sales.payments.method")}</Label>
            <select id="refund-method" className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={method} disabled={credit.isPending} onChange={(e) => setMethod(e.target.value as (typeof PAYMENT_METHODS)[number])}>
              {PAYMENT_METHODS.map((m) => <option key={m} value={m}>{t(`sales.payments.methods.${m}`)}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="refund-reference">{t("sales.payments.reference")}</Label>
            <Input id="refund-reference" value={reference} maxLength={200} disabled={credit.isPending} onChange={(e) => setReference(e.target.value)} />
          </div>
        </fieldset> : null}
        <p className="text-sm text-muted-foreground">{t("sales.credit.irreversible")}</p>
        {credit.error ? <p role="alert" className="text-sm text-destructive">{describeError(credit.error)}</p> : null}
      </form>
      <DialogFooter>
        <Button variant="outline" disabled={credit.isPending} onClick={onClose}>{t("common.cancel")}</Button>
        <Button type="submit" form="invoice-credit" variant="destructive" disabled={!ready}>{t("sales.credit.submit")}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}
