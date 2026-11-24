import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { SalesDocument, SalesPayment } from "@/api/types"
import { ConfirmDialog } from "@/components/common/ConfirmDialog"
import { MoneyText } from "@/components/common/MoneyText"
import { PermissionGate } from "@/components/common/PermissionGate"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { toastError } from "@/lib/errors"
import { useVoidPayment } from "../hooks"
import { VOID_PAYMENT } from "../model"

/** Payment history of an invoice. Admins can void a payment entered in error, which reopens the balance. */
export function PaymentsSection({ document: d, onConflict }: { document: SalesDocument; onConflict: () => void }) {
  const { t } = useTranslation()
  const [voiding, setVoiding] = useState<SalesPayment | null>(null)
  const mutation = useVoidPayment(d.id)
  const payments = d.payments ?? []
  if (!payments.length) return null
  const canVoid = d.status === "issued" || d.status === "paid"
  return <section className="space-y-2" aria-label={t("sales.payments.title")}>
    <h3 className="font-medium">{t("sales.payments.title")}</h3>
    <Table>
      <caption className="sr-only">{t("sales.payments.title")}</caption>
      <TableHeader><TableRow>
        <TableHead>{t("sales.payments.date")}</TableHead><TableHead>{t("sales.payments.method")}</TableHead>
        <TableHead>{t("sales.payments.reference")}</TableHead><TableHead>{t("sales.payments.amountShort")}</TableHead>
        <TableHead className="print:hidden"><span className="sr-only">{t("sales.payments.actions")}</span></TableHead>
      </TableRow></TableHeader>
      <TableBody>{payments.map((p) => <TableRow key={p.id} className={p.voided_at ? "text-muted-foreground line-through" : undefined}>
        <TableCell>{p.paid_on}</TableCell><TableCell>{t(`sales.payments.methods.${p.method}`)}</TableCell>
        <TableCell className="whitespace-normal">{p.reference ?? "—"}</TableCell>
        <TableCell><MoneyText value={{ amount_minor: p.amount_minor, currency: d.currency }} />{p.voided_at ? <span className="ml-2 no-underline">{t("sales.payments.voided")}</span> : null}</TableCell>
        <TableCell className="print:hidden">{!p.voided_at && canVoid ? <PermissionGate permission={VOID_PAYMENT}>
          <Button variant="outline" size="sm" disabled={mutation.isPending} onClick={() => setVoiding(p)}>{t("sales.payments.void")}</Button>
        </PermissionGate> : null}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
    <ConfirmDialog open={voiding !== null} onOpenChange={(open) => { if (!open) setVoiding(null) }} destructive pending={mutation.isPending}
      title={t("sales.payments.confirmVoid.title")} description={t("sales.payments.confirmVoid.text")} confirmLabel={t("sales.payments.void")}
      onConfirm={() => {
        if (!voiding) return
        mutation.mutateAsync({ paymentId: voiding.id, version: d.version }).then(
          () => { setVoiding(null); toast.success(t("sales.payments.voidDone")) },
          (err: unknown) => { setVoiding(null); toastError(err, onConflict) },
        )
      }} />
  </section>
}
